"""
Parser de fichas técnicas de recetas en PDF (formato exportado desde otro
software: título, categoría, tabla de ingredientes con peso/formato,
procedimiento y raciones).

Detecta las columnas de la tabla por el TEXTO de sus encabezados (no por
posiciones fijas en píxeles), para adaptarse aunque el ancho de columnas
varíe levemente entre PDFs distintos, siempre que mantengan la misma
estructura general.
"""
import re
import difflib
import pdfplumber

CATEGORIA_TO_TIPO = {
    'PORCION': 'porcion', 'PORCIONES': 'porcion',
    'ENSALADA': 'ensalada', 'ENSALADAS': 'ensalada',
    'ENTRADA': 'entrada', 'ENTRADAS': 'entrada',
    'PLATO CALIENTE': 'plato_caliente', 'PLATOS CALIENTES': 'plato_caliente',
    'PLATO': 'plato_caliente',
}

RENDIMIENTO_UNIDAD_MAP = {
    'gramos': 'g', 'gramo': 'g', 'g': 'g',
    'kilos': 'kg', 'kilo': 'kg', 'kg': 'kg', 'kilogramos': 'kg',
    'mililitros': 'ml', 'mililitro': 'ml', 'ml': 'ml',
    'litros': 'l', 'litro': 'l', 'l': 'l',
    'unidad': 'u', 'unidades': 'u', 'u': 'u',
}

FORMATO_TO_UNIDAD = {
    'gramos': 'g', 'gramo': 'g', 'g': 'g', 'kg': 'kg', 'kilos': 'kg', 'kilogramos': 'kg',
    'mililitros': 'ml', 'mililitro': 'ml', 'ml': 'ml', 'litros': 'l', 'litro': 'l',
    'unidad': 'u', 'unidades': 'u', 'u': 'u',
}


def es_categoria_semielaborado(categoria):
    key = (categoria or '').strip().upper()
    return 'ELABORADO' in key  # cubre "ELABORADOS", "SEMIELABORADOS", "SEMI ELABORADOS"


STOPWORDS_MINUSCULA = {'de', 'del', 'la', 'las', 'el', 'los', 'y', 'con', 'en', 'a', 'al', 'o', 'para', 'sin'}


def _titulo_natural(texto):
    """Como .title() pero sin poner en mayúscula preposiciones/artículos
    (ej: 'Cebolla De Verdeo' -> 'Cebolla de Verdeo')."""
    palabras = texto.strip().title().split()
    return ' '.join(p if p.lower() not in STOPWORDS_MINUSCULA else p.lower() for p in palabras)


def _norm(s):
    if not s:
        return ''
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def categoria_to_tipo(categoria):
    key = (categoria or '').strip().upper()
    if key in CATEGORIA_TO_TIPO:
        return CATEGORIA_TO_TIPO[key]
    # fallback: buscar coincidencia parcial
    for k, v in CATEGORIA_TO_TIPO.items():
        if k in key or key in k:
            return v
    return None  # no se pudo determinar, hay que preguntarle al usuario


def formato_to_unidad(formato):
    key = (formato or '').strip().lower()
    return FORMATO_TO_UNIDAD.get(key, 'g')


def parse_receta_pdf(path):
    """Extrae título, categoría, ingredientes, procedimiento y raciones de un PDF
    con el formato de ficha técnica esperado. Devuelve un dict, o {'error': ...}
    si no se pudo reconocer la estructura (ej: PDF escaneado sin texto)."""
    try:
        with pdfplumber.open(path) as pdf:
            all_words = []
            for page in pdf.pages:
                all_words.extend(page.extract_words())
    except Exception as e:
        return {'error': f'No se pudo abrir el PDF: {e}'}

    if not all_words:
        return {'error': 'No se pudo extraer texto del PDF (¿es una imagen escaneada?)'}

    def find_word(pred):
        for w in all_words:
            if pred(w['text']):
                return w
        return None

    h_ingrediente = find_word(lambda t: t.upper() == 'INGREDIENTE')
    h_peso = find_word(lambda t: t.upper() == 'PESO')
    h_formato = find_word(lambda t: t.upper() == 'FORMATO')
    h_procedimiento = find_word(lambda t: t.upper() == 'PROCEDIMIENTO')
    h_raciones = find_word(lambda t: t.upper() == 'RACIONES')
    h_cantidad = find_word(lambda t: t.upper() == 'CANTIDAD')
    h_resultante = find_word(lambda t: 'RESULTANTE' in t.upper())
    h_appcc = find_word(lambda t: 'APPCC' in t.upper())

    if not h_ingrediente or not h_peso or not h_formato:
        return {'error': 'No se encontró la tabla de ingredientes (encabezados Ingrediente/Peso neto/Formato). ¿Es una ficha de este mismo formato?'}

    x_name = h_ingrediente['x0']
    x_peso = h_peso['x0']
    x_formato = h_formato['x0']
    x_procedimiento = h_procedimiento['x0'] if h_procedimiento else 99999
    y_header = h_ingrediente['top']
    # límite inferior de la tabla de ingredientes y del procedimiento: lo que venga primero
    # entre "Raciones" (fichas de producto) o "Cantidad resultante" (fichas de semielaborado)
    y_fin_tabla = min(h_raciones['top'] if h_raciones else 99999,
                       h_resultante['top'] if h_resultante else 99999)

    # Titulo: texto arriba de todo (antes de "CATEGORÍA")
    h_categoria = find_word(lambda t: 'CATEGOR' in t.upper())
    y_categoria = h_categoria['top'] if h_categoria else y_header
    titulo_words = [w for w in all_words if w['top'] < y_categoria - 2]
    titulo = ' '.join(w['text'] for w in sorted(titulo_words, key=lambda w: (w['top'], w['x0'])))

    categoria = ''
    if h_categoria:
        cat_words = [w for w in all_words if abs(w['top'] - h_categoria['top']) < 3 and w['text'] != h_categoria['text']]
        categoria = ' '.join(w['text'] for w in sorted(cat_words, key=lambda w: w['x0']))

    # Filas de ingredientes
    ing_words = [w for w in all_words
                 if w['top'] > y_header + 3 and w['top'] < y_fin_tabla - 2 and w['x0'] < x_procedimiento - 5]

    rows = []
    for w in sorted(ing_words, key=lambda w: w['top']):
        placed = False
        for row in rows:
            if abs(row['words'][0]['top'] - w['top']) < 6:
                row['words'].append(w)
                placed = True
                break
        if not placed:
            rows.append({'words': [w]})

    ingredientes = []
    for row in rows:
        name_parts = [w['text'] for w in row['words'] if w['x0'] < x_peso - 5]
        peso_parts = [w['text'] for w in row['words'] if x_peso - 5 <= w['x0'] < x_formato - 5]
        formato_parts = [w['text'] for w in row['words'] if w['x0'] >= x_formato - 5]
        name = ' '.join(name_parts).strip()
        peso_txt = ' '.join(peso_parts).strip()
        formato = ' '.join(formato_parts).strip()
        if not name:
            continue
        try:
            peso = float(peso_txt.replace(',', '.'))
        except ValueError:
            peso = None
        ingredientes.append({'nombre': _titulo_natural(name), 'peso_neto': peso, 'formato': formato,
                              'unidad': formato_to_unidad(formato)})

    # Procedimiento
    proc_words = [w for w in all_words
                  if w['x0'] >= x_procedimiento - 5
                  and w['top'] > (h_procedimiento['top'] + 3 if h_procedimiento else y_header)
                  and w['top'] < y_fin_tabla - 2]
    proc_lines = []
    for w in sorted(proc_words, key=lambda w: w['top']):
        placed = False
        for line in proc_lines:
            if abs(line['y'] - w['top']) < 5:
                line['words'].append(w)
                placed = True
                break
        if not placed:
            proc_lines.append({'y': w['top'], 'words': [w]})
    procedimiento_texto = ' '.join(
        ' '.join(w['text'] for w in sorted(line['words'], key=lambda w: w['x0']))
        for line in proc_lines
    )
    pasos = re.split(r'(?=\b\d+\s*\.-)', procedimiento_texto)
    pasos = [p.strip() for p in pasos if p.strip()]

    # Raciones / Cantidad (fichas de "otro producto")
    raciones = None
    cantidad = None
    if h_raciones and h_cantidad:
        rac_row_words = [w for w in all_words if w['top'] > h_raciones['top'] + 3]
        rac_candidates = [w for w in rac_row_words if abs(w['x0'] - h_raciones['x0']) < 60]
        cant_candidates = [w for w in rac_row_words if abs(w['x0'] - h_cantidad['x0']) < 60]
        if rac_candidates:
            raciones = rac_candidates[0]['text']
        if cant_candidates:
            try:
                cantidad = float(cant_candidates[0]['text'].replace(',', '.'))
            except ValueError:
                pass

    es_semi = es_categoria_semielaborado(categoria)
    tipo = None if es_semi else categoria_to_tipo(categoria)

    rendimiento_cantidad = None
    rendimiento_unidad = None
    vida_util_dias = None
    if es_semi:
        # "Cantidad resultante: 7.4 kilos" — numero y unidad aparecen debajo/al lado de la etiqueta
        if h_resultante:
            rend_words = [w for w in all_words
                          if h_resultante['top'] - 3 <= w['top'] <= h_resultante['top'] + 15
                          and w['x0'] > h_resultante['x0'] + len(h_resultante['text'])]
            rend_words_sorted = sorted(rend_words, key=lambda w: w['x0'])
            if rend_words_sorted:
                try:
                    rendimiento_cantidad = float(rend_words_sorted[0]['text'].replace(',', '.'))
                except ValueError:
                    pass
                if len(rend_words_sorted) > 1:
                    unidad_txt = rend_words_sorted[1]['text'].strip().lower()
                    rendimiento_unidad = RENDIMIENTO_UNIDAD_MAP.get(unidad_txt, unidad_txt)

        # "Comentario APPCC" -> "Vida Util: N dias"
        if h_appcc:
            appcc_words = [w for w in all_words if w['top'] > h_appcc['top'] + 3]
            appcc_lines = []
            for w in sorted(appcc_words, key=lambda w: w['top']):
                placed = False
                for line in appcc_lines:
                    if abs(line['y'] - w['top']) < 5:
                        line['words'].append(w)
                        placed = True
                        break
                if not placed:
                    appcc_lines.append({'y': w['top'], 'words': [w]})
            texto_appcc = ' '.join(
                ' '.join(w['text'] for w in sorted(line['words'], key=lambda w: w['x0']))
                for line in appcc_lines
            )
            m = re.search(r'vida\s*util\s*:?\s*(\d+(?:[.,]\d+)?)', texto_appcc, re.IGNORECASE)
            if m:
                try:
                    vida_util_dias = int(float(m.group(1).replace(',', '.')))
                except ValueError:
                    pass

    return {
        'titulo': _titulo_natural(titulo),
        'categoria': categoria,
        'es_semielaborado': es_semi,
        'tipo': tipo,
        'rendimiento_cantidad': rendimiento_cantidad,
        'rendimiento_unidad': rendimiento_unidad,
        'vida_util_dias': vida_util_dias,
        'ingredientes': ingredientes,
        'procedimiento_pasos': pasos,
        'raciones': raciones,
        'cantidad': cantidad,
    }


def match_insumo(nombre_ingrediente, insumos_catalogo, umbral=0.72):
    """Busca el insumo más parecido en el catálogo (lista de dicts con 'key' y 'label').
    Devuelve {'key':..., 'label':..., 'score':...} del mejor candidato si supera el
    umbral de similitud, o None si no hay ninguno suficientemente parecido."""
    n_ing = _norm(nombre_ingrediente)
    if not n_ing:
        return None
    best = None
    best_score = 0
    for ins in insumos_catalogo:
        n_label = _norm(ins.get('label', ''))
        if not n_label:
            continue
        score = difflib.SequenceMatcher(None, n_ing, n_label).ratio()
        # bonus si uno contiene literalmente al otro (ej: "cebolla morada" en "cebolla morada picada")
        if n_ing in n_label or n_label in n_ing:
            score = max(score, 0.85)
        if score > best_score:
            best_score = score
            best = ins
    if best and best_score >= umbral:
        return {'key': best['key'], 'label': best['label'], 'score': round(best_score, 2)}
    return None
