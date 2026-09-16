from flask import Flask, request, jsonify, send_file, render_template, session, redirect, url_for
import json, os, tempfile, functools
import psycopg2
import psycopg2.extras
from datetime import datetime
from gen_pdf import build_pdf
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'CAMBIAR-ESTA-CLAVE-EN-PRODUCCION')
# Railway (y la mayoria de plataformas cloud) terminan el HTTPS en su proxy y
# reenvian a la app como HTTP interno. Sin esto, url_for(..., _external=True)
# generaria URLs http:// en vez de https://, rompiendo el callback de Google OAuth.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# DATABASE_URL la provee Railway automaticamente al agregar un servicio de PostgreSQL
# y vincularlo a esta app. Es una base de datos administrada: los datos NO se pierden
# con cada deploy, a diferencia de un archivo SQLite en el filesystem del contenedor.
DATABASE_URL = os.environ.get('DATABASE_URL')

# ── Postgres IntegrityError, expuesto con el mismo nombre que usa el resto del codigo ──
IntegrityError = psycopg2.IntegrityError

# ── DB ────────────────────────────────────────────────────────────────────
class _PGCursorWrapper:
    """Envuelve un cursor de psycopg2 para que .execute() acepte '?' como
    placeholder (estilo sqlite3) y devuelva filas con acceso tipo diccionario,
    manteniendo compatible el resto del codigo sin reescribir cada consulta."""
    def __init__(self, cursor):
        self._cursor = cursor
    def execute(self, sql, params=()):
        sql_pg = sql.replace('?', '%s')
        self._cursor.execute(sql_pg, params)
        return self
    def executescript(self, script):
        self._cursor.execute(script)
        return self
    def fetchone(self):
        return self._cursor.fetchone()
    def fetchall(self):
        return self._cursor.fetchall()
    @property
    def lastrowid(self):
        return None  # no usado; los INSERT que necesitan el id usan RETURNING

class _PGConnWrapper:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql_pg = sql.replace('?', '%s')
        cur.execute(sql_pg, params)
        return _PGCursorWrapper(cur)
    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(script)
        return _PGCursorWrapper(cur)
    def cursor(self):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return _PGCursorWrapper(cur)
    def commit(self):
        self._conn.commit()
    def rollback(self):
        self._conn.rollback()
    def close(self):
        self._conn.close()

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return _PGConnWrapper(conn)

# ── Google OAuth setup ──────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS rolls (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            insumos TEXT NOT NULL,
            piezas_por_rollo INTEGER NOT NULL DEFAULT 14
        );
        CREATE TABLE IF NOT EXISTS combos (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            family TEXT NOT NULL,
            rolls TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semielaborados (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            insumo_key TEXT NOT NULL,
            unit TEXT NOT NULL,
            rolls TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sushimanes (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            productivity INTEGER NOT NULL DEFAULT 10,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS insumos (
            id SERIAL PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            unidad_receta TEXT NOT NULL DEFAULT 'g',
            unidad_resumen TEXT NOT NULL DEFAULT 'kg',
            factor_conversion REAL NOT NULL DEFAULT 0.001,
            precio_unidad REAL
        );
        CREATE TABLE IF NOT EXISTS marcas (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            nombre TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS familias (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS otros_productos (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'porcion',
            familia TEXT,
            insumos TEXT NOT NULL DEFAULT '{}',
            marcas TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS equivalencias (
            id SERIAL PRIMARY KEY,
            tipo TEXT NOT NULL,
            nombre_canonico TEXT NOT NULL,
            nombre_alias TEXT NOT NULL,
            marca TEXT,
            UNIQUE(tipo, nombre_alias)
        );
    ''')
    conn.commit()

    # Bootstrap: si no hay usuarios todavía, crear el admin inicial
    # a partir de la variable de entorno ADMIN_EMAIL
    admin_email = os.environ.get('ADMIN_EMAIL')
    n_users = c.execute('SELECT COUNT(*) AS cnt FROM usuarios').fetchone()['cnt']
    if n_users == 0 and admin_email:
        c.execute('INSERT INTO usuarios (email, nombre, role, active, created_at) VALUES (?,?,?,1,?) ON CONFLICT (email) DO NOTHING',
                   (admin_email.strip().lower(), 'Administrador', 'admin', datetime.now().isoformat()))
        conn.commit()

    # Sembrar familias a partir de las que ya usan los combos existentes
    # (para no romper nada) mas categorias sugeridas para los productos nuevos
    n_familias = c.execute('SELECT COUNT(*) AS cnt FROM familias').fetchone()['cnt']
    if n_familias == 0:
        existing_families = [r['family'] for r in c.execute(
            "SELECT DISTINCT family FROM combos WHERE family IS NOT NULL AND family != ''").fetchall()]
        suggested = ['Porciones', 'Ensaladas', 'Entradas', 'Platos Calientes', 'Otros']
        all_families = list(dict.fromkeys(existing_families + suggested))  # dedup preservando orden
        for fam in all_families:
            c.execute('INSERT INTO familias (name) VALUES (?) ON CONFLICT (name) DO NOTHING', (fam,))
        conn.commit()

    # Migration: add recipe columns to semielaborados if they don't exist yet
    def get_columns(table):
        rows = c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,)).fetchall()
        return [r['column_name'] for r in rows]

    existing_cols = get_columns('semielaborados')
    if 'receta' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN receta TEXT NOT NULL DEFAULT '[]'")
    if 'rendimiento_cantidad' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN rendimiento_cantidad REAL NOT NULL DEFAULT 0")
    if 'rendimiento_unidad' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN rendimiento_unidad TEXT NOT NULL DEFAULT 'g'")
    if 'marcas' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN marcas TEXT NOT NULL DEFAULT '[]'")

    # Migration: add 'marcas' column to combos and rolls
    combo_cols = get_columns('combos')
    if 'marcas' not in combo_cols:
        c.execute("ALTER TABLE combos ADD COLUMN marcas TEXT NOT NULL DEFAULT '[]'")

    roll_cols = get_columns('rolls')
    if 'marcas' not in roll_cols:
        c.execute("ALTER TABLE rolls ADD COLUMN marcas TEXT NOT NULL DEFAULT '[]'")
    if 'piezas_por_rollo' not in roll_cols:
        c.execute("ALTER TABLE rolls ADD COLUMN piezas_por_rollo INTEGER NOT NULL DEFAULT 14")

    conn.commit()

    # Seed if empty
    if c.execute('SELECT COUNT(*) AS cnt FROM rolls').fetchone()['cnt'] == 0:
        seed_path = os.path.join(os.path.dirname(__file__), 'data', 'seed.json')
        if os.path.exists(seed_path):
            with open(seed_path) as f:
                seed = json.load(f)
            for roll in seed.get('rolls', []):
                c.execute('INSERT INTO rolls (name, insumos) VALUES (?,?) ON CONFLICT (name) DO NOTHING',
                          (roll['name'], json.dumps(roll['insumos'])))
            for combo in seed.get('combos', []):
                c.execute('INSERT INTO combos (name, family, rolls) VALUES (?,?,?) ON CONFLICT (name) DO NOTHING',
                          (combo['name'], combo['family'], json.dumps(combo['rolls'])))
            for semi in seed.get('semielaborados', []):
                c.execute('INSERT INTO semielaborados (name, insumo_key, unit, rolls) VALUES (?,?,?,?) ON CONFLICT (name) DO NOTHING',
                          (semi['name'], semi['insumo_key'], semi['unit'], json.dumps(semi['rolls'])))
            for sm in seed.get('sushimanes', []):
                c.execute('INSERT INTO sushimanes (name, productivity) VALUES (?,?) ON CONFLICT (name) DO NOTHING',
                          (sm['name'], sm['productivity']))
            for ins in seed.get('insumos', []):
                c.execute('''INSERT INTO insumos
                    (key,label,unidad_receta,unidad_resumen,factor_conversion,precio_unidad)
                    VALUES (?,?,?,?,?,?) ON CONFLICT (key) DO NOTHING''',
                    (ins['key'], ins['label'], ins['unidad_receta'],
                     ins['unidad_resumen'], ins['factor_conversion'], ins.get('precio_unidad')))
            conn.commit()
    conn.close()

# ── Auth helpers ─────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'No autenticado'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'No autenticado'}), 401
            return redirect('/login')
        if session.get('user_role') != 'admin':
            return jsonify({'error': 'Requiere permisos de administrador'}), 403
        return f(*args, **kwargs)
    return decorated

# ── Auth routes ──────────────────────────────────────────────────────────
@app.route('/debug/env')
def debug_env():
    """Ruta temporal de diagnostico. Borrar despues de resolver el problema de login."""
    def mask(val, keep_start=12, keep_end=8):
        if not val:
            return 'NO CONFIGURADA (vacia o None)'
        val = str(val)
        if len(val) <= keep_start + keep_end:
            return f'(muy corta, {len(val)} caracteres) {val[:3]}...'
        return f'{val[:keep_start]}...{val[-keep_end:]}  (longitud total: {len(val)})'

    client_id = os.environ.get('GOOGLE_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
    secret_key = os.environ.get('FLASK_SECRET_KEY')
    admin_email = os.environ.get('ADMIN_EMAIL')
    db_url = os.environ.get('DATABASE_URL')

    db_status = 'NO CONFIGURADA — la app no puede guardar datos de forma persistente sin esto'
    db_conn_test = 'N/A'
    if db_url:
        db_status = mask(db_url, keep_start=15, keep_end=10)
        try:
            test_conn = get_db()
            test_conn.execute('SELECT 1')
            test_conn.close()
            db_conn_test = 'OK — se pudo conectar y ejecutar una consulta de prueba'
        except Exception as e:
            db_conn_test = f'ERROR al conectar: {e}'

    lines = [
        f"DATABASE_URL: {db_status}",
        f"  -> prueba de conexion: {db_conn_test}",
        "",
        f"GOOGLE_CLIENT_ID: {mask(client_id)}",
        f"  -> termina en .apps.googleusercontent.com: {str(client_id).strip().endswith('.apps.googleusercontent.com') if client_id else 'N/A'}",
        f"  -> tiene espacios al inicio/final sin recortar: {(client_id != client_id.strip()) if client_id else 'N/A'}",
        "",
        f"GOOGLE_CLIENT_SECRET: {'CONFIGURADA (longitud ' + str(len(client_secret)) + ')' if client_secret else 'NO CONFIGURADA'}",
        "",
        f"FLASK_SECRET_KEY: {'CONFIGURADA' if secret_key else 'NO CONFIGURADA (usando valor por defecto, inseguro)'}",
        "",
        f"ADMIN_EMAIL: {admin_email if admin_email else 'NO CONFIGURADA'}",
        "",
        f"URL de callback que la app va a pedirle a Google: {url_for('auth_callback', _external=True)}",
    ]
    return "<pre style='font-family:monospace;font-size:14px;padding:20px'>" + "\n".join(lines) + "</pre>"

@app.route('/login')
def login_page():
    if 'user_email' in session:
        return redirect('/')
    return render_template('login.html')

@app.route('/auth/google')
def auth_google():
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    if not user_info or not user_info.get('email'):
        return render_template('login.html', error='No se pudo verificar tu cuenta de Google. Intentá de nuevo.')

    email = user_info['email'].strip().lower()
    conn = get_db()
    user = conn.execute('SELECT * FROM usuarios WHERE email=?', (email,)).fetchone()
    conn.close()

    if not user or not user['active']:
        return render_template('login.html',
            error=f'La cuenta {email} no tiene acceso autorizado. Pedile a un administrador que te agregue.')

    session['user_email'] = email
    session['user_name'] = user_info.get('name', email)
    session['user_picture'] = user_info.get('picture', '')
    session['user_role'] = user['role']
    return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── Routes ────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    return render_template('index.html',
        is_admin=(session.get('user_role')=='admin'),
        user_name=session.get('user_name',''),
        user_email=session.get('user_email',''),
        user_picture=session.get('user_picture',''))

# ── Usuarios (solo admin) ──
@app.route('/api/usuarios', methods=['GET'])
@admin_required
def get_usuarios():
    conn = get_db()
    rows = conn.execute('SELECT * FROM usuarios ORDER BY email').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'email': r['email'], 'nombre': r['nombre'],
                     'role': r['role'], 'active': bool(r['active'])} for r in rows])

@app.route('/api/usuarios', methods=['POST'])
@admin_required
def create_usuario():
    data = request.json
    email = data['email'].strip().lower()
    conn = get_db()
    try:
        conn.execute('INSERT INTO usuarios (email, nombre, role, active, created_at) VALUES (?,?,?,?,?)',
                     (email, data.get('nombre',''), data.get('role','user'),
                      int(data.get('active', True)), datetime.now().isoformat()))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ese email ya está registrado'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/usuarios/<int:usuario_id>', methods=['PUT'])
@admin_required
def update_usuario(usuario_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE usuarios SET email=?, nombre=?, role=?, active=? WHERE id=?',
                     (data['email'].strip().lower(), data.get('nombre',''), data.get('role','user'),
                      int(data.get('active', True)), usuario_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ese email ya está registrado en otro usuario'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/usuarios/<int:usuario_id>', methods=['DELETE'])
@admin_required
def delete_usuario(usuario_id):
    conn = get_db()
    # Evitar que el admin se borre a si mismo y se quede afuera
    row = conn.execute('SELECT email FROM usuarios WHERE id=?', (usuario_id,)).fetchone()
    if row and row['email'] == session.get('user_email'):
        conn.close()
        return jsonify({'error': 'No podés eliminar tu propio usuario mientras estás conectado'}), 400
    conn.execute('DELETE FROM usuarios WHERE id=?', (usuario_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Respaldo completo (backup / restore) — solo admin ──────────────────────
@app.route('/api/backup', methods=['GET'])
@admin_required
def backup_data():
    conn = get_db()
    TABLES = ['combos', 'rolls', 'semielaborados', 'sushimanes', 'insumos', 'marcas', 'usuarios', 'familias', 'otros_productos']
    data = {'version': 1, 'exported_at': datetime.now().isoformat()}
    for table in TABLES:
        rows = conn.execute(f'SELECT * FROM {table}').fetchall()
        data[table] = [dict(r) for r in rows]
    conn.close()

    resp = jsonify(data)
    filename = f'backup_sushi_{datetime.now().strftime("%Y%m%d_%H%M")}.json'
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp

@app.route('/api/restore', methods=['POST'])
@admin_required
def restore_data():
    data = request.json
    if not data or 'version' not in data:
        return jsonify({'error': 'El archivo no parece ser un respaldo válido'}), 400

    conn = get_db()
    counts = {}

    def upsert(table, rows, unique_col, insert_cols):
        n = 0
        for row in rows:
            key_val = row.get(unique_col)
            if key_val is None:
                continue
            existing = conn.execute(f'SELECT id FROM {table} WHERE {unique_col}=?', (key_val,)).fetchone()
            values = [row.get(c) for c in insert_cols]
            if existing:
                set_clause = ', '.join(f'{c}=?' for c in insert_cols)
                conn.execute(f'UPDATE {table} SET {set_clause} WHERE id=?', values + [existing['id']])
            else:
                cols_clause = ', '.join(insert_cols)
                placeholders = ', '.join('?' for _ in insert_cols)
                conn.execute(f'INSERT INTO {table} ({cols_clause}) VALUES ({placeholders})', values)
            n += 1
        return n

    counts['combos'] = upsert('combos', data.get('combos', []), 'name',
                               ['name', 'family', 'rolls', 'marcas'])
    # Backups viejos (de antes de agregar "piezas por rollo") no tienen este campo;
    # les asignamos 14 por defecto para no romper la restauracion.
    for r in data.get('rolls', []):
        if r.get('piezas_por_rollo') is None:
            r['piezas_por_rollo'] = 14
    counts['rolls'] = upsert('rolls', data.get('rolls', []), 'name',
                              ['name', 'insumos', 'marcas', 'piezas_por_rollo'])
    counts['semielaborados'] = upsert('semielaborados', data.get('semielaborados', []), 'name',
                              ['name', 'insumo_key', 'unit', 'rolls', 'receta',
                               'rendimiento_cantidad', 'rendimiento_unidad', 'marcas'])
    counts['sushimanes'] = upsert('sushimanes', data.get('sushimanes', []), 'name',
                              ['name', 'productivity', 'active'])
    counts['insumos'] = upsert('insumos', data.get('insumos', []), 'key',
                              ['key', 'label', 'unidad_receta', 'unidad_resumen',
                               'factor_conversion', 'precio_unidad'])
    counts['marcas'] = upsert('marcas', data.get('marcas', []), 'name', ['name'])
    counts['usuarios'] = upsert('usuarios', data.get('usuarios', []), 'email',
                              ['email', 'nombre', 'role', 'active', 'created_at'])
    counts['familias'] = upsert('familias', data.get('familias', []), 'name', ['name'])
    counts['otros_productos'] = upsert('otros_productos', data.get('otros_productos', []), 'name',
                              ['name', 'tipo', 'familia', 'insumos', 'marcas'])

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'counts': counts})

# ── Familias ──
@app.route('/api/familias', methods=['GET'])
@login_required
def get_familias():
    conn = get_db()
    rows = conn.execute('SELECT * FROM familias ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])

@app.route('/api/familias', methods=['POST'])
@admin_required
def create_familia():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO familias (name) VALUES (?)', (data['name'].strip(),))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Esa familia ya existe'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/familias/<int:familia_id>', methods=['PUT'])
@admin_required
def update_familia(familia_id):
    data = request.json
    conn = get_db()
    old = conn.execute('SELECT name FROM familias WHERE id=?', (familia_id,)).fetchone()
    new_name = data['name'].strip()
    try:
        conn.execute('UPDATE familias SET name=? WHERE id=?', (new_name, familia_id))
        # Si se renombra, actualizamos las referencias existentes en combos y otros_productos
        if old and old['name'] != new_name:
            conn.execute('UPDATE combos SET family=? WHERE family=?', (new_name, old['name']))
            conn.execute('UPDATE otros_productos SET familia=? WHERE familia=?', (new_name, old['name']))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otra familia con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/familias/<int:familia_id>', methods=['DELETE'])
@admin_required
def delete_familia(familia_id):
    conn = get_db()
    conn.execute('DELETE FROM familias WHERE id=?', (familia_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Otros productos (porciones, ensaladas, entradas, platos calientes) ──
@app.route('/api/otros-productos', methods=['GET'])
@login_required
def get_otros_productos():
    conn = get_db()
    rows = conn.execute('SELECT * FROM otros_productos ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'tipo': r['tipo'],
                     'familia': r['familia'], 'insumos': json.loads(r['insumos']),
                     'marcas': json.loads(r['marcas'] or '[]')} for r in rows])

@app.route('/api/otros-productos', methods=['POST'])
@admin_required
def create_otro_producto():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO otros_productos (name, tipo, familia, insumos, marcas) VALUES (?,?,?,?,?)',
                     (data['name'], data.get('tipo','porcion'), data.get('familia',''),
                      json.dumps(data.get('insumos', {})), json.dumps(data.get('marcas', []))))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un producto con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/otros-productos/<int:producto_id>', methods=['PUT'])
@admin_required
def update_otro_producto(producto_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE otros_productos SET name=?, tipo=?, familia=?, insumos=?, marcas=? WHERE id=?',
                     (data['name'], data.get('tipo','porcion'), data.get('familia',''),
                      json.dumps(data.get('insumos', {})), json.dumps(data.get('marcas', [])), producto_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro producto con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/otros-productos/<int:producto_id>', methods=['DELETE'])
@admin_required
def delete_otro_producto(producto_id):
    conn = get_db()
    conn.execute('DELETE FROM otros_productos WHERE id=?', (producto_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Equivalencias (mismo producto, distinto nombre por marca) ──
@app.route('/api/equivalencias', methods=['GET'])
@login_required
def get_equivalencias():
    conn = get_db()
    rows = conn.execute('SELECT * FROM equivalencias ORDER BY tipo, nombre_canonico, nombre_alias').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'tipo': r['tipo'], 'nombre_canonico': r['nombre_canonico'],
                     'nombre_alias': r['nombre_alias'], 'marca': r['marca']} for r in rows])

@app.route('/api/equivalencias', methods=['POST'])
@admin_required
def create_equivalencia():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO equivalencias (tipo, nombre_canonico, nombre_alias, marca) VALUES (?,?,?,?)',
                     (data['tipo'], data['nombre_canonico'], data['nombre_alias'].strip(), data.get('marca', '')))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ese alias ya está usado para otro producto de ese tipo'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/equivalencias/<int:equiv_id>', methods=['PUT'])
@admin_required
def update_equivalencia(equiv_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE equivalencias SET tipo=?, nombre_canonico=?, nombre_alias=?, marca=? WHERE id=?',
                     (data['tipo'], data['nombre_canonico'], data['nombre_alias'].strip(), data.get('marca', ''), equiv_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ese alias ya está usado para otro producto de ese tipo'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/equivalencias/<int:equiv_id>', methods=['DELETE'])
@admin_required
def delete_equivalencia(equiv_id):
    conn = get_db()
    conn.execute('DELETE FROM equivalencias WHERE id=?', (equiv_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/equivalencias/sync', methods=['POST'])
@admin_required
def sync_equivalencias():
    """Reemplaza TODAS las equivalencias de un tipo (combo/roll/otro_producto)
    con el estado actual de la matriz producto x marca que manda el frontend.
    Las celdas vacias simplemente no generan fila."""
    data = request.json
    tipo = data.get('tipo')
    entries = data.get('entries', [])  # [{nombre_canonico, marca, nombre_alias}]
    if tipo not in ('combo', 'roll', 'otro_producto'):
        return jsonify({'error': 'Tipo inválido'}), 400

    conn = get_db()
    conn.execute('DELETE FROM equivalencias WHERE tipo=?', (tipo,))
    count = 0
    for e in entries:
        alias = (e.get('nombre_alias') or '').strip()
        if not alias:
            continue
        conn.execute('INSERT INTO equivalencias (tipo, nombre_canonico, nombre_alias, marca) VALUES (?,?,?,?)',
                     (tipo, e['nombre_canonico'], alias, e.get('marca', '')))
        count += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'count': count})

# ── Rolls ──
@app.route('/api/rolls', methods=['GET'])
@login_required
def get_rolls():
    conn = get_db()
    rows = conn.execute('SELECT * FROM rolls ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'],
                     'insumos': json.loads(r['insumos']),
                     'marcas': json.loads(r['marcas'] or '[]'),
                     'piezas_por_rollo': r['piezas_por_rollo']} for r in rows])

@app.route('/api/rolls', methods=['POST'])
@admin_required
def create_roll():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO rolls (name, insumos, marcas, piezas_por_rollo) VALUES (?,?,?,?)',
                     (data['name'], json.dumps(data['insumos']), json.dumps(data.get('marcas', [])),
                      data.get('piezas_por_rollo', 14)))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un roll con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/rolls/<int:roll_id>', methods=['PUT'])
@admin_required
def update_roll(roll_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE rolls SET name=?, insumos=?, marcas=?, piezas_por_rollo=? WHERE id=?',
                     (data['name'], json.dumps(data['insumos']), json.dumps(data.get('marcas', [])),
                      data.get('piezas_por_rollo', 14), roll_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro roll con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/rolls/<int:roll_id>', methods=['DELETE'])
@admin_required
def delete_roll(roll_id):
    conn = get_db()
    conn.execute('DELETE FROM rolls WHERE id=?', (roll_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Combos ──
@app.route('/api/combos', methods=['GET'])
@login_required
def get_combos():
    conn = get_db()
    rows = conn.execute('SELECT * FROM combos ORDER BY family, name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'family': r['family'],
                     'rolls': json.loads(r['rolls']),
                     'marcas': json.loads(r['marcas'] or '[]')} for r in rows])

@app.route('/api/combos', methods=['POST'])
@admin_required
def create_combo():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO combos (name, family, rolls, marcas) VALUES (?,?,?,?)',
                     (data['name'], data['family'], json.dumps(data['rolls']), json.dumps(data.get('marcas', []))))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un combo con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/combos/<int:combo_id>', methods=['PUT'])
@admin_required
def update_combo(combo_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE combos SET name=?, family=?, rolls=?, marcas=? WHERE id=?',
                     (data['name'], data['family'], json.dumps(data['rolls']), json.dumps(data.get('marcas', [])), combo_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro combo con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/combos/<int:combo_id>', methods=['DELETE'])
@admin_required
def delete_combo(combo_id):
    conn = get_db()
    conn.execute('DELETE FROM combos WHERE id=?', (combo_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Semielaborados ──
@app.route('/api/semielaborados', methods=['GET'])
@login_required
def get_semielaborados():
    conn = get_db()
    rows = conn.execute('SELECT * FROM semielaborados ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'insumo_key': r['insumo_key'],
                     'unit': r['unit'], 'rolls': json.loads(r['rolls']),
                     'receta': json.loads(r['receta'] or '[]'),
                     'rendimiento_cantidad': r['rendimiento_cantidad'],
                     'rendimiento_unidad': r['rendimiento_unidad'],
                     'marcas': json.loads(r['marcas'] or '[]')} for r in rows])

@app.route('/api/semielaborados', methods=['POST'])
@admin_required
def create_semi():
    data = request.json
    conn = get_db()
    try:
        conn.execute('''INSERT INTO semielaborados
                        (name, insumo_key, unit, rolls, receta, rendimiento_cantidad, rendimiento_unidad, marcas)
                        VALUES (?,?,?,?,?,?,?,?)''',
                     (data['name'], data['insumo_key'], data['unit'], json.dumps(data['rolls']),
                      json.dumps(data.get('receta', [])),
                      data.get('rendimiento_cantidad', 0),
                      data.get('rendimiento_unidad', 'g'),
                      json.dumps(data.get('marcas', []))))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un semielaborado con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/semielaborados/<int:semi_id>', methods=['PUT'])
@admin_required
def update_semi(semi_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('''UPDATE semielaborados SET name=?, insumo_key=?, unit=?, rolls=?,
                        receta=?, rendimiento_cantidad=?, rendimiento_unidad=?, marcas=? WHERE id=?''',
                     (data['name'], data['insumo_key'], data['unit'], json.dumps(data['rolls']),
                      json.dumps(data.get('receta', [])),
                      data.get('rendimiento_cantidad', 0),
                      data.get('rendimiento_unidad', 'g'),
                      json.dumps(data.get('marcas', [])), semi_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro semielaborado con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/semielaborados/<int:semi_id>', methods=['DELETE'])
@admin_required
def delete_semi(semi_id):
    conn = get_db()
    conn.execute('DELETE FROM semielaborados WHERE id=?', (semi_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Sushimanes ──
@app.route('/api/sushimanes', methods=['GET'])
@login_required
def get_sushimanes():
    conn = get_db()
    rows = conn.execute('SELECT * FROM sushimanes ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'],
                     'productivity': r['productivity'],
                     'active': bool(r['active'])} for r in rows])

@app.route('/api/sushimanes', methods=['POST'])
@admin_required
def create_sushiman():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO sushimanes (name, productivity) VALUES (?,?)',
                     (data['name'], data.get('productivity', 10)))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un sushiman con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/sushimanes/<int:sm_id>', methods=['PUT'])
@admin_required
def update_sushiman(sm_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE sushimanes SET name=?, productivity=?, active=? WHERE id=?',
                     (data['name'], data['productivity'], int(data.get('active', True)), sm_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro sushiman con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/sushimanes/<int:sm_id>', methods=['DELETE'])
@admin_required
def delete_sushiman(sm_id):
    conn = get_db()
    conn.execute('DELETE FROM sushimanes WHERE id=?', (sm_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Marcas ──
@app.route('/api/marcas', methods=['GET'])
@login_required
def get_marcas():
    conn = get_db()
    rows = conn.execute('SELECT * FROM marcas ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])

@app.route('/api/marcas', methods=['POST'])
@admin_required
def create_marca():
    data = request.json
    conn = get_db()
    try:
        conn.execute('INSERT INTO marcas (name) VALUES (?)', (data['name'],))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Esa marca ya existe'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/marcas/<int:marca_id>', methods=['PUT'])
@admin_required
def update_marca(marca_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('UPDATE marcas SET name=? WHERE id=?', (data['name'], marca_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otra marca con ese nombre'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/marcas/<int:marca_id>', methods=['DELETE'])
@admin_required
def delete_marca(marca_id):
    conn = get_db()
    conn.execute('DELETE FROM marcas WHERE id=?', (marca_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Insumos (tabla maestra de unidades) ──
@app.route('/api/insumos', methods=['GET'])
@login_required
def get_insumos():
    conn = get_db()
    rows = conn.execute('SELECT * FROM insumos ORDER BY label').fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'], 'key': r['key'], 'label': r['label'],
        'unidad_receta': r['unidad_receta'], 'unidad_resumen': r['unidad_resumen'],
        'factor_conversion': r['factor_conversion'], 'precio_unidad': r['precio_unidad']
    } for r in rows])

@app.route('/api/insumos', methods=['POST'])
@admin_required
def create_insumo():
    data = request.json
    conn = get_db()
    try:
        conn.execute('''INSERT INTO insumos (key,label,unidad_receta,unidad_resumen,factor_conversion,precio_unidad)
                        VALUES (?,?,?,?,?,?)''',
                     (data['key'], data['label'], data['unidad_receta'], data['unidad_resumen'],
                      data['factor_conversion'], data.get('precio_unidad')))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe un insumo con esa clave'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/insumos/<int:ins_id>', methods=['PUT'])
@admin_required
def update_insumo(ins_id):
    data = request.json
    conn = get_db()
    try:
        conn.execute('''UPDATE insumos SET key=?, label=?, unidad_receta=?, unidad_resumen=?,
                        factor_conversion=?, precio_unidad=? WHERE id=?''',
                     (data['key'], data['label'], data['unidad_receta'], data['unidad_resumen'],
                      data['factor_conversion'], data.get('precio_unidad'), ins_id))
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Ya existe otro insumo con esa clave'}), 400
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/insumos/<int:ins_id>', methods=['DELETE'])
@admin_required
def delete_insumo(ins_id):
    conn = get_db()
    conn.execute('DELETE FROM insumos WHERE id=?', (ins_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Calcular producción ──
@app.route('/api/calcular', methods=['POST'])
@login_required
def calcular():
    body       = request.json
    sales      = body.get('sales', [])
    adj        = body.get('adjustments', {})
    adj_mode   = body.get('adjMode', 'pct')
    global_pct = body.get('globalPct', 100)

    conn = get_db()
    combos_db = {r['name']: json.loads(r['rolls'])
                 for r in conn.execute('SELECT name, rolls FROM combos').fetchall()}
    rolls_db  = {r['name']: json.loads(r['insumos'])
                 for r in conn.execute('SELECT name, insumos FROM rolls').fetchall()}
    roll_piezas_db = {r['name']: r['piezas_por_rollo']
                 for r in conn.execute('SELECT name, piezas_por_rollo FROM rolls').fetchall()}
    otros_db  = {r['name']: {'tipo': r['tipo'], 'insumos': json.loads(r['insumos'])}
                 for r in conn.execute('SELECT name, tipo, insumos FROM otros_productos').fetchall()}
    semis_db  = conn.execute('SELECT * FROM semielaborados').fetchall()
    insumos_master = {r['key']: dict(r) for r in conn.execute('SELECT * FROM insumos').fetchall()}

    # Equivalencias: mismo producto, distinto nombre segun la marca que lo vendio
    def norm(s):
        return (s or '').lower().replace(' ', '').replace('-', '').replace('_', '')

    equiv_rows = conn.execute('SELECT tipo, nombre_canonico, nombre_alias FROM equivalencias').fetchall()
    equiv_combo = {norm(r['nombre_alias']): r['nombre_canonico'] for r in equiv_rows if r['tipo'] == 'combo'}
    equiv_otro  = {norm(r['nombre_alias']): r['nombre_canonico'] for r in equiv_rows if r['tipo'] == 'otro_producto'}
    equiv_roll  = {norm(r['nombre_alias']): r['nombre_canonico'] for r in equiv_rows if r['tipo'] == 'roll'}
    conn.close()

    roll_totals = {}
    otros_totals = {}  # nombre -> {tipo, qty}
    direct_insumo_totals = {}  # insumos que vienen DIRECTO de "otros productos" (no de rolls)

    for s in sales:
        a = adj.get(s['code'], 0)
        adj_qty = max(0, round(s['qty'] + (s['qty'] * a / 100 if adj_mode == 'pct' else a)))
        final_qty = round(adj_qty * global_pct / 100)
        if final_qty <= 0:
            continue

        combo = combos_db.get(s['name']) or combos_db.get(s['code'])
        if not combo:
            name_lower = s['name'].lower().replace(' ', '')
            for cname, crolls in combos_db.items():
                if cname.lower().replace(' ', '') == name_lower:
                    combo = crolls
                    break
        if not combo:
            # Ver si el nombre/codigo es un alias conocido de otra marca para un combo existente
            canonico = equiv_combo.get(norm(s['name'])) or equiv_combo.get(norm(s['code']))
            if canonico:
                combo = combos_db.get(canonico)
        if combo:
            for roll_name, piezas in combo.items():
                if not piezas:
                    continue
                roll_name = equiv_roll.get(norm(roll_name), roll_name)
                piezas_por_rollo = roll_piezas_db.get(roll_name, 14) or 14
                rolls_needed = -(-piezas * final_qty // piezas_por_rollo)
                roll_totals[roll_name] = roll_totals.get(roll_name, 0) + rolls_needed
            continue

        # No es combo: ver si matchea con "otros productos" (porciones, ensaladas, entradas, platos calientes)
        otro = otros_db.get(s['name']) or otros_db.get(s['code'])
        if not otro:
            name_lower = s['name'].lower().replace(' ', '')
            for pname, pdata in otros_db.items():
                if pname.lower().replace(' ', '') == name_lower:
                    otro = pdata
                    break
        if not otro:
            # Ver si el nombre/codigo es un alias conocido de otra marca para un producto existente
            canonico = equiv_otro.get(norm(s['name'])) or equiv_otro.get(norm(s['code']))
            if canonico:
                otro = otros_db.get(canonico)
        if otro:
            key = None
            for pname, pdata in otros_db.items():
                if pdata is otro:
                    key = pname
                    break
            if key:
                if key not in otros_totals:
                    otros_totals[key] = {'tipo': otro['tipo'], 'qty': 0}
                otros_totals[key]['qty'] += final_qty
            for k, v in otro['insumos'].items():
                if v:
                    direct_insumo_totals[k] = direct_insumo_totals.get(k, 0) + v * final_qty

    production = sorted(
        [{'name': k, 'qty': v, 'piezasPorRollo': roll_piezas_db.get(k, 14) or 14,
          'piezas': v * (roll_piezas_db.get(k, 14) or 14)} for k, v in roll_totals.items() if v > 0],
        key=lambda x: -x['qty']
    )
    otros_productos_out = sorted(
        [{'name': k, 'tipo': v['tipo'], 'qty': v['qty']} for k, v in otros_totals.items() if v['qty'] > 0],
        key=lambda x: -x['qty']
    )

    # Insumos — usar tabla maestra para labels/unidades si existe
    FALLBACK_LABELS = {
        'salmon':'Salmón','queso':'Queso crema','palta':'Palta',
        'langos':'Langostinos rebozados','algas':'Algas','arroz':'Arroz',
        'grill':'Grill','tartar':'Tartar de salmón','kanikama':'Kanikama',
        'batata':'Hilos de batata','guac':'Guacamole','spicy':'Salsa spicy',
        'okinawa':'Manga okinawa','salmonCrispy':'Salmón crispy'
    }
    insumo_totals = {}
    for r in production:
        recipe = rolls_db.get(r['name'], {})
        for k, v in recipe.items():
            if v:
                insumo_totals[k] = insumo_totals.get(k, 0) + v * r['qty']
    # Sumar tambien lo que aportan directo los "otros productos"
    for k, v in direct_insumo_totals.items():
        insumo_totals[k] = insumo_totals.get(k, 0) + v

    insumos_out = {}
    for k, total in sorted(insumo_totals.items(), key=lambda x: -x[1]):
        master = insumos_master.get(k)
        if master:
            label = master['label']
            factor = master['factor_conversion']
            unidad_resumen = master['unidad_resumen']
            converted = total * factor
            display = f"{round(total)} {master['unidad_receta']} / {converted:.2f} {unidad_resumen}"
        else:
            label = FALLBACK_LABELS.get(k, k)
            unit = 'hojas' if k == 'algas' else ('u' if k == 'langos' else 'g')
            if unit == 'g':
                display = f"{round(total)} g / {total/1000:.2f} kg"
            elif unit == 'hojas':
                display = f"{total:.1f} hojas"
            else:
                display = f"{round(total)} u"
        insumos_out[k] = {
            'label': label,
            'total': round(total, 1) if total < 100 else round(total),
            'display': display
        }

    # Semielaborados — se detectan automáticamente según qué rolls producidos
    # (o "otros productos": porciones, ensaladas, entradas, platos calientes)
    # incluyen su insumo_key en su receta (no depende de una lista manual)
    semis_out = []
    for semi in semis_db:
        total = 0
        used_in = []
        for prod in production:
            recipe = rolls_db.get(prod['name'], {})
            amt = recipe.get(semi['insumo_key'], 0)
            if amt:
                total += amt * prod['qty']
                used_in.append(prod['name'])
        for otro in otros_productos_out:
            recipe = otros_db.get(otro['name'], {}).get('insumos', {})
            amt = recipe.get(semi['insumo_key'], 0)
            if amt:
                total += amt * otro['qty']
                used_in.append(otro['name'])
        if total > 0:
            unit = semi['unit']
            display = (f"{round(total)} g / {total/1000:.2f} kg" if unit == 'g'
                       else f"{round(total)} u")

            semi_out = {
                'name': semi['name'],
                'usedIn': ', '.join(used_in),
                'display': display,
                'total': round(total)
            }

            # Si tiene receta cargada, calcular lotes e ingredientes crudos
            receta = json.loads(semi['receta'] or '[]')
            rend_cant = semi['rendimiento_cantidad'] or 0
            rend_unid = semi['rendimiento_unidad'] or unit
            if receta and rend_cant > 0:
                scale = total / rend_cant
                batches = -(-scale // 1)  # ceil
                semi_out['receta'] = {
                    'rendimiento_cantidad': rend_cant,
                    'rendimiento_unidad': rend_unid,
                    'lotes_necesarios': int(batches),
                    'escala_exacta': round(scale, 2),
                    'ingredientes': [
                        {
                            'nombre': ing['nombre'],
                            'cantidad_receta': ing['cantidad'],
                            'unidad': ing['unidad'],
                            'cantidad_total': round(ing['cantidad'] * scale, 1)
                        } for ing in receta
                    ]
                }
            semis_out.append(semi_out)

    return jsonify({
        'production': production,
        'insumos': insumos_out,
        'semis': semis_out,
        'otrosProductos': otros_productos_out
    })

# ── Generar PDF ──
@app.route('/api/pdf', methods=['POST'])
@login_required
def generar_pdf():
    body = request.json
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.close()
    build_pdf(body, tmp.name)
    date_str = body.get('date', datetime.now().strftime('%d-%m-%Y'))
    return send_file(tmp.name, as_attachment=True,
                     download_name=f'produccion_{date_str}.pdf',
                     mimetype='application/pdf')

# ── Importar recetas desde Excel ──────────────────────────────────────────
@app.route('/api/importar/rolls', methods=['POST'])
@admin_required
def importar_rolls():
    if 'file' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'El archivo debe ser .xlsx o .xls'}), 400

    try:
        from openpyxl import load_workbook
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        file.save(tmp.name)
        tmp.close()

        wb = load_workbook(tmp.name, data_only=True)
        os.unlink(tmp.name)

        ws = None
        for name in wb.sheetnames:
            if 'ROLL' in name.upper():
                ws = wb[name]
                break
        if not ws:
            return jsonify({'error': 'No se encontró la solapa ROLLS en el archivo'}), 400

        headers = []
        for cell in ws[3]:
            v = cell.value
            headers.append(str(v).strip() if v else '')

        HEADER_MAP = {
            'salmón (g)':'salmon','salmon (g)':'salmon',
            'queso crema (g)':'queso','queso (g)':'queso',
            'palta (g)':'palta',
            'langostinos (u)':'langos','langostinos rebozados (u)':'langos',
            'algas (hojas)':'algas',
            'arroz (g)':'arroz',
            'grill (g)':'grill',
            'tartar salmón (g)':'tartar','tartar salmon (g)':'tartar','tartar (g)':'tartar',
            'kanikama (g)':'kanikama',
            'batata (g)':'batata','hilos de batata (g)':'batata',
            'guacamole (g)':'guac','guac (g)':'guac',
            'salsa spicy (g)':'spicy','spicy (g)':'spicy',
            'manga okinawa (g)':'okinawa','okinawa (g)':'okinawa',
            'atún (g)':'atun','atun (g)':'atun',
            'camarón (g)':'camaron','camaron (g)':'camaron',
            'mayonesa (g)':'mayonesa','ciboulette (g)':'ciboulette',
            'pepino (g)':'pepino','zanahoria (g)':'zanahoria',
            'pimiento (g)':'pimiento','cream cheese (g)':'cream_cheese',
            'nori extra (hojas)':'nori','sésamo (g)':'sesamo','sesamo (g)':'sesamo',
        }

        conn = get_db()
        creados, actualizados = [], []

        for row in ws.iter_rows(min_row=4, values_only=True):
            nombre = row[0] if row[0] else None
            if not nombre or str(nombre).strip() == '' or 'EJEMPLO' in str(nombre).upper():
                continue
            nombre = str(nombre).strip()

            piezas_por_rollo = 14
            if len(row) > 1 and row[1] is not None:
                try:
                    piezas_por_rollo = int(float(row[1]))
                except (ValueError, TypeError):
                    pass

            insumos = {}
            for ci, header in enumerate(headers[2:], 2):
                if ci >= len(row): break
                val = row[ci]
                if val is None: continue
                try: val = float(val)
                except: continue
                if val <= 0: continue
                h_lower = header.lower().strip()
                key = HEADER_MAP.get(h_lower, h_lower.replace(' ','_').replace('(','').replace(')','').strip('_'))
                if key: insumos[key] = val

            existing = conn.execute('SELECT id FROM rolls WHERE LOWER(name)=LOWER(?)', (nombre,)).fetchone()
            if existing:
                conn.execute('UPDATE rolls SET insumos=?, piezas_por_rollo=? WHERE id=?',
                             (json.dumps(insumos), piezas_por_rollo, existing['id']))
                actualizados.append(nombre)
            else:
                conn.execute('INSERT INTO rolls (name, insumos, piezas_por_rollo) VALUES (?,?,?)',
                             (nombre, json.dumps(insumos), piezas_por_rollo))
                creados.append(nombre)

        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'creados': creados, 'actualizados': actualizados,
                        'total': len(creados) + len(actualizados)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/importar/semielaborados', methods=['POST'])
@admin_required
def importar_semielaborados():
    if 'file' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'El archivo debe ser .xlsx o .xls'}), 400

    try:
        from openpyxl import load_workbook
        tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        file.save(tmp.name)
        tmp.close()

        wb = load_workbook(tmp.name, data_only=True)
        os.unlink(tmp.name)

        conn = get_db()
        creados, actualizados = [], []

        for sheet_name in wb.sheetnames:
            if 'NUEVO' in sheet_name.upper():
                continue

            ws = wb[sheet_name]
            semi_name = sheet_name.strip()
            ingredientes = []
            rendimiento = None
            unidad_rend = 'g'

            for row in ws.iter_rows(min_row=4, values_only=True):
                if not any(row): continue
                ing_name = row[0]
                if ing_name is None: continue
                ing_str = str(ing_name).strip()

                if 'RENDIMIENTO' in ing_str.upper():
                    try:
                        rendimiento = float(row[1]) if row[1] else 0
                        unidad_rend = str(row[2]).strip() if row[2] else 'g'
                    except: pass
                    break

                if ing_str.upper() in ('INGREDIENTE','') or ing_str.startswith('🍣'):
                    continue

                try:
                    cant = float(row[1]) if row[1] is not None else 0
                    unid = str(row[2]).strip() if row[2] else 'g'
                except:
                    cant, unid = 0, 'g'

                if cant > 0 and ing_str:
                    ingredientes.append({'nombre': ing_str, 'cantidad': cant, 'unidad': unid})

            if not ingredientes and rendimiento is None:
                continue

            KEY_MAP = {
                'tartar': 'tartar', 'grill': 'grill', 'langostino': 'langos',
                'guacamole': 'guac', 'batata': 'batata', 'kanikama': 'kanikama',
                'spicy': 'spicy', 'okinawa': 'okinawa', 'salmon crispy': 'salmonCrispy',
            }
            insumo_key = semi_name.lower().replace(' ','_')
            for k, v in KEY_MAP.items():
                if k in semi_name.lower():
                    insumo_key = v
                    break

            receta_json = json.dumps(ingredientes)

            existing = conn.execute('SELECT id FROM semielaborados WHERE LOWER(name)=LOWER(?)', (semi_name,)).fetchone()
            rolls_json = json.dumps([])
            if existing:
                ex_full = conn.execute('SELECT rolls FROM semielaborados WHERE id=?', (existing['id'],)).fetchone()
                rolls_json = ex_full['rolls'] if ex_full else '[]'
                conn.execute('''UPDATE semielaborados SET insumo_key=?, unit=?, rolls=?,
                                receta=?, rendimiento_cantidad=?, rendimiento_unidad=? WHERE id=?''',
                            (insumo_key, unidad_rend, rolls_json, receta_json,
                             rendimiento or 0, unidad_rend, existing['id']))
                actualizados.append(semi_name)
            else:
                conn.execute('''INSERT INTO semielaborados
                                (name, insumo_key, unit, rolls, receta, rendimiento_cantidad, rendimiento_unidad)
                                VALUES (?,?,?,?,?,?,?)''',
                            (semi_name, insumo_key, unidad_rend, rolls_json, receta_json,
                             rendimiento or 0, unidad_rend))
                creados.append(semi_name)

        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'creados': creados, 'actualizados': actualizados,
                        'total': len(creados) + len(actualizados)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Main ──────────────────────────────────────────────────────────────────
init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
