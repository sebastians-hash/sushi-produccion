from flask import Flask, request, jsonify, send_file, render_template
import sqlite3, json, os, tempfile
from datetime import datetime
from gen_pdf import build_pdf

app = Flask(__name__)
DB = os.path.join(os.path.dirname(__file__), 'data', 'sushi.db')

# ── DB ────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS rolls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            insumos TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS combos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            family TEXT NOT NULL,
            rolls TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semielaborados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            insumo_key TEXT NOT NULL,
            unit TEXT NOT NULL,
            rolls TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sushimanes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            productivity INTEGER NOT NULL DEFAULT 10,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS insumos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            unidad_receta TEXT NOT NULL DEFAULT 'g',
            unidad_resumen TEXT NOT NULL DEFAULT 'kg',
            factor_conversion REAL NOT NULL DEFAULT 0.001,
            precio_unidad REAL
        );
    ''')
    conn.commit()

    # Migration: add recipe columns to semielaborados if they don't exist yet
    existing_cols = [r['name'] for r in c.execute('PRAGMA table_info(semielaborados)').fetchall()]
    if 'receta' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN receta TEXT NOT NULL DEFAULT '[]'")
    if 'rendimiento_cantidad' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN rendimiento_cantidad REAL NOT NULL DEFAULT 0")
    if 'rendimiento_unidad' not in existing_cols:
        c.execute("ALTER TABLE semielaborados ADD COLUMN rendimiento_unidad TEXT NOT NULL DEFAULT 'g'")
    conn.commit()

    # Seed if empty
    if c.execute('SELECT COUNT(*) FROM rolls').fetchone()[0] == 0:
        seed_path = os.path.join(os.path.dirname(__file__), 'data', 'seed.json')
        if os.path.exists(seed_path):
            with open(seed_path) as f:
                seed = json.load(f)
            for roll in seed.get('rolls', []):
                c.execute('INSERT OR IGNORE INTO rolls (name, insumos) VALUES (?,?)',
                          (roll['name'], json.dumps(roll['insumos'])))
            for combo in seed.get('combos', []):
                c.execute('INSERT OR IGNORE INTO combos (name, family, rolls) VALUES (?,?,?)',
                          (combo['name'], combo['family'], json.dumps(combo['rolls'])))
            for semi in seed.get('semielaborados', []):
                c.execute('INSERT OR IGNORE INTO semielaborados (name, insumo_key, unit, rolls) VALUES (?,?,?,?)',
                          (semi['name'], semi['insumo_key'], semi['unit'], json.dumps(semi['rolls'])))
            for sm in seed.get('sushimanes', []):
                c.execute('INSERT OR IGNORE INTO sushimanes (name, productivity) VALUES (?,?)',
                          (sm['name'], sm['productivity']))
            for ins in seed.get('insumos', []):
                c.execute('''INSERT OR IGNORE INTO insumos
                    (key,label,unidad_receta,unidad_resumen,factor_conversion,precio_unidad)
                    VALUES (?,?,?,?,?,?)''',
                    (ins['key'], ins['label'], ins['unidad_receta'],
                     ins['unidad_resumen'], ins['factor_conversion'], ins.get('precio_unidad')))
            conn.commit()
    conn.close()

# ── Routes ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── Rolls ──
@app.route('/api/rolls', methods=['GET'])
def get_rolls():
    conn = get_db()
    rows = conn.execute('SELECT * FROM rolls ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'],
                     'insumos': json.loads(r['insumos'])} for r in rows])

@app.route('/api/rolls', methods=['POST'])
def create_roll():
    data = request.json
    conn = get_db()
    conn.execute('INSERT INTO rolls (name, insumos) VALUES (?,?)',
                 (data['name'], json.dumps(data['insumos'])))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/rolls/<int:roll_id>', methods=['PUT'])
def update_roll(roll_id):
    data = request.json
    conn = get_db()
    conn.execute('UPDATE rolls SET name=?, insumos=? WHERE id=?',
                 (data['name'], json.dumps(data['insumos']), roll_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/rolls/<int:roll_id>', methods=['DELETE'])
def delete_roll(roll_id):
    conn = get_db()
    conn.execute('DELETE FROM rolls WHERE id=?', (roll_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Combos ──
@app.route('/api/combos', methods=['GET'])
def get_combos():
    conn = get_db()
    rows = conn.execute('SELECT * FROM combos ORDER BY family, name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'family': r['family'],
                     'rolls': json.loads(r['rolls'])} for r in rows])

@app.route('/api/combos', methods=['POST'])
def create_combo():
    data = request.json
    conn = get_db()
    conn.execute('INSERT INTO combos (name, family, rolls) VALUES (?,?,?)',
                 (data['name'], data['family'], json.dumps(data['rolls'])))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/combos/<int:combo_id>', methods=['PUT'])
def update_combo(combo_id):
    data = request.json
    conn = get_db()
    conn.execute('UPDATE combos SET name=?, family=?, rolls=? WHERE id=?',
                 (data['name'], data['family'], json.dumps(data['rolls']), combo_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/combos/<int:combo_id>', methods=['DELETE'])
def delete_combo(combo_id):
    conn = get_db()
    conn.execute('DELETE FROM combos WHERE id=?', (combo_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Semielaborados ──
@app.route('/api/semielaborados', methods=['GET'])
def get_semielaborados():
    conn = get_db()
    rows = conn.execute('SELECT * FROM semielaborados ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'insumo_key': r['insumo_key'],
                     'unit': r['unit'], 'rolls': json.loads(r['rolls']),
                     'receta': json.loads(r['receta'] or '[]'),
                     'rendimiento_cantidad': r['rendimiento_cantidad'],
                     'rendimiento_unidad': r['rendimiento_unidad']} for r in rows])

@app.route('/api/semielaborados', methods=['POST'])
def create_semi():
    data = request.json
    conn = get_db()
    conn.execute('''INSERT INTO semielaborados
                    (name, insumo_key, unit, rolls, receta, rendimiento_cantidad, rendimiento_unidad)
                    VALUES (?,?,?,?,?,?,?)''',
                 (data['name'], data['insumo_key'], data['unit'], json.dumps(data['rolls']),
                  json.dumps(data.get('receta', [])),
                  data.get('rendimiento_cantidad', 0),
                  data.get('rendimiento_unidad', 'g')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/semielaborados/<int:semi_id>', methods=['PUT'])
def update_semi(semi_id):
    data = request.json
    conn = get_db()
    conn.execute('''UPDATE semielaborados SET name=?, insumo_key=?, unit=?, rolls=?,
                    receta=?, rendimiento_cantidad=?, rendimiento_unidad=? WHERE id=?''',
                 (data['name'], data['insumo_key'], data['unit'], json.dumps(data['rolls']),
                  json.dumps(data.get('receta', [])),
                  data.get('rendimiento_cantidad', 0),
                  data.get('rendimiento_unidad', 'g'), semi_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/semielaborados/<int:semi_id>', methods=['DELETE'])
def delete_semi(semi_id):
    conn = get_db()
    conn.execute('DELETE FROM semielaborados WHERE id=?', (semi_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Sushimanes ──
@app.route('/api/sushimanes', methods=['GET'])
def get_sushimanes():
    conn = get_db()
    rows = conn.execute('SELECT * FROM sushimanes ORDER BY name').fetchall()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'],
                     'productivity': r['productivity'],
                     'active': bool(r['active'])} for r in rows])

@app.route('/api/sushimanes', methods=['POST'])
def create_sushiman():
    data = request.json
    conn = get_db()
    conn.execute('INSERT INTO sushimanes (name, productivity) VALUES (?,?)',
                 (data['name'], data.get('productivity', 10)))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/sushimanes/<int:sm_id>', methods=['PUT'])
def update_sushiman(sm_id):
    data = request.json
    conn = get_db()
    conn.execute('UPDATE sushimanes SET name=?, productivity=?, active=? WHERE id=?',
                 (data['name'], data['productivity'], int(data.get('active', True)), sm_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/sushimanes/<int:sm_id>', methods=['DELETE'])
def delete_sushiman(sm_id):
    conn = get_db()
    conn.execute('DELETE FROM sushimanes WHERE id=?', (sm_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Insumos (tabla maestra de unidades) ──
@app.route('/api/insumos', methods=['GET'])
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
def create_insumo():
    data = request.json
    conn = get_db()
    conn.execute('''INSERT INTO insumos (key,label,unidad_receta,unidad_resumen,factor_conversion,precio_unidad)
                    VALUES (?,?,?,?,?,?)''',
                 (data['key'], data['label'], data['unidad_receta'], data['unidad_resumen'],
                  data['factor_conversion'], data.get('precio_unidad')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/insumos/<int:ins_id>', methods=['PUT'])
def update_insumo(ins_id):
    data = request.json
    conn = get_db()
    conn.execute('''UPDATE insumos SET key=?, label=?, unidad_receta=?, unidad_resumen=?,
                    factor_conversion=?, precio_unidad=? WHERE id=?''',
                 (data['key'], data['label'], data['unidad_receta'], data['unidad_resumen'],
                  data['factor_conversion'], data.get('precio_unidad'), ins_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/insumos/<int:ins_id>', methods=['DELETE'])
def delete_insumo(ins_id):
    conn = get_db()
    conn.execute('DELETE FROM insumos WHERE id=?', (ins_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── Calcular producción ──
@app.route('/api/calcular', methods=['POST'])
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
    semis_db  = conn.execute('SELECT * FROM semielaborados').fetchall()
    insumos_master = {r['key']: dict(r) for r in conn.execute('SELECT * FROM insumos').fetchall()}
    conn.close()

    roll_totals = {}
    for s in sales:
        a = adj.get(s['code'], 0)
        adj_qty = max(0, round(s['qty'] + (s['qty'] * a / 100 if adj_mode == 'pct' else a)))
        final_qty = round(adj_qty * global_pct / 100)
        combo = combos_db.get(s['name']) or combos_db.get(s['code'])
        if not combo:
            name_lower = s['name'].lower().replace(' ', '')
            for cname, crolls in combos_db.items():
                if cname.lower().replace(' ', '') == name_lower:
                    combo = crolls
                    break
        if not combo:
            continue
        for roll_name, piezas in combo.items():
            if not piezas:
                continue
            rolls_needed = -(-piezas * final_qty // 14)
            roll_totals[roll_name] = roll_totals.get(roll_name, 0) + rolls_needed

    production = sorted(
        [{'name': k, 'qty': v} for k, v in roll_totals.items() if v > 0],
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

    # Semielaborados
    semis_out = []
    for semi in semis_db:
        semi_rolls = json.loads(semi['rolls'])
        total = 0
        used_in = []
        for rn in semi_rolls:
            prod = next((p for p in production if p['name'] == rn), None)
            recipe = rolls_db.get(rn, {})
            amt = recipe.get(semi['insumo_key'], 0)
            if prod and amt:
                total += amt * prod['qty']
                used_in.append(rn)
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
        'semis': semis_out
    })

# ── Generar PDF ──
@app.route('/api/pdf', methods=['POST'])
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
                conn.execute('UPDATE rolls SET insumos=? WHERE id=?', (json.dumps(insumos), existing['id']))
                actualizados.append(nombre)
            else:
                conn.execute('INSERT INTO rolls (name, insumos) VALUES (?,?)', (nombre, json.dumps(insumos)))
                creados.append(nombre)

        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'creados': creados, 'actualizados': actualizados,
                        'total': len(creados) + len(actualizados)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/importar/semielaborados', methods=['POST'])
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
