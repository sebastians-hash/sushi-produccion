import json, sys, os
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

A4_P = A4
A4_L = landscape(A4)
MARGIN = 12 * mm

BLACK   = colors.HexColor('#111111')
DARK    = colors.HexColor('#222222')
MED     = colors.HexColor('#555555')
BG_GRAY = colors.HexColor('#F4F4F4')
BG_DARK = colors.HexColor('#5F5E5A')
WHITE   = colors.white
ACCENT  = colors.HexColor('#E8E0D0')
PURPLE  = colors.HexColor('#6b3fa0')
PURPLE_BG = colors.HexColor('#f5f0fc')
WARN_BG = colors.HexColor('#FFF3CD')

def S(name, **kw):
    base = dict(fontName='Helvetica', fontSize=10, textColor=DARK, leading=13)
    base.update(kw)
    return ParagraphStyle(name, **base)

def section_header(title, width):
    t = Table([[Paragraph(title, S('sh', fontName='Helvetica-Bold',
        fontSize=11, textColor=WHITE, leading=14))]],
        colWidths=[width])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), BG_DARK),
        ('TOPPADDING',    (0,0),(-1,-1), 6),
        ('BOTTOMPADDING', (0,0),(-1,-1), 6),
        ('LEFTPADDING',   (0,0),(-1,-1), 10),
    ]))
    return t

def cover_band(date_str, global_pct, width, label=None, turno=None):
    title_style = S('dt', fontName='Helvetica-Bold', fontSize=18,
                    textColor=WHITE, leading=22)
    sub_style   = S('ds', fontSize=9, textColor=colors.HexColor('#DDDDDD'),
                    alignment=TA_RIGHT, leading=14)

    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'kata_logo_square.png')
    logo_h = 30
    logo_w = logo_h  # el logo de Kata es cuadrado (1:1)

    if os.path.exists(logo_path):
        logo_cell = RLImage(logo_path, width=logo_w, height=logo_h)
        left_col_w = logo_w + 16
    else:
        logo_cell = Paragraph("KATA", title_style)
        left_col_w = width * 0.25

    right_w = width - left_col_w - 4*mm
    turno_txt = f"  |  Turno {turno}" if turno else ""
    t = Table([[ logo_cell,
                 Paragraph("PLANILLA DE PRODUCCION", title_style),
                 Paragraph(f"{date_str}{turno_txt}  |  {global_pct}% de la venta", sub_style) ]],
              colWidths=[left_col_w, right_w*0.6, right_w*0.4])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), BG_DARK),
        ('TOPPADDING',    (0,0),(-1,-1), 8),
        ('BOTTOMPADDING', (0,0),(-1,-1), 8),
        ('LEFTPADDING',   (0,0),(0,0),  10),
        ('RIGHTPADDING',  (-1,0),(-1,-1),12),
        ('LEFTPADDING',   (1,0),(1,0),  10),
        ('VALIGN',        (0,0),(-1,-1),'MIDDLE'),
    ]))
    if not label:
        return t

    label_style = S('lb', fontName='Helvetica-Bold', fontSize=11, textColor=PURPLE, leading=14)
    lt = Table([[Paragraph(label, label_style)]], colWidths=[width])
    lt.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,-1), PURPLE_BG),
        ('TOPPADDING', (0,0),(-1,-1), 6), ('BOTTOMPADDING', (0,0),(-1,-1), 6),
        ('LEFTPADDING', (0,0),(-1,-1), 10),
        ('LINEBELOW', (0,0),(-1,-1), 0.5, PURPLE),
    ]))
    wrap = Table([[t],[lt]], colWidths=[width])
    wrap.setStyle(TableStyle([
        ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),0),
    ]))
    return wrap
    return t

def metrics_row(combos_total, rolls_total, piezas_total, global_pct, width):
    items = [
        ("COMBOS",  str(combos_total), "unidades"),
        ("ROLLOS",  str(rolls_total),  "total"),
        ("PIEZAS",  str(piezas_total), "total"),
        ("% PROD.", f"{global_pct}%",  "sobre venta"),
    ]
    cw = width / 4
    cells = []
    for label, val, unit in items:
        cells.append(Table([
            [Paragraph(label, S('ml', fontSize=8, textColor=MED))],
            [Paragraph(val,   S('mv', fontName='Helvetica-Bold', fontSize=18, textColor=BLACK, leading=22))],
            [Paragraph(unit,  S('mu', fontSize=8, textColor=MED))],
        ], colWidths=[cw - 6]))
    row_t = Table([cells], colWidths=[cw]*4)
    row_t.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(-1,-1), BG_GRAY),
        ('BOX',           (0,0),(-1,-1), 0.4, colors.HexColor('#CCCCCC')),
        ('INNERGRID',     (0,0),(-1,-1), 0.4, colors.HexColor('#CCCCCC')),
        ('TOPPADDING',    (0,0),(-1,-1), 5),
        ('BOTTOMPADDING', (0,0),(-1,-1), 5),
        ('LEFTPADDING',   (0,0),(-1,-1), 8),
    ]))
    return row_t

def _make_table(data, col_widths, fs, header_bg, stripe):
    lh = fs + 3
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ('FONTNAME',      (0,0),(-1,-1), 'Helvetica'),
        ('FONTSIZE',      (0,0),(-1,-1), fs),
        ('LEADING',       (0,0),(-1,-1), lh),
        ('TOPPADDING',    (0,0),(-1,-1), 3),
        ('BOTTOMPADDING', (0,0),(-1,-1), 3),
        ('LEFTPADDING',   (0,0),(-1,-1), 5),
        ('RIGHTPADDING',  (0,0),(-1,-1), 5),
        ('GRID',          (0,0),(-1,-1), 0.3, colors.HexColor('#CCCCCC')),
        ('BACKGROUND',    (0,0),(-1, 0), header_bg),
        ('FONTNAME',      (0,0),(-1, 0), 'Helvetica-Bold'),
    ]
    if stripe:
        style.append(('ROWBACKGROUNDS', (0,1),(-1,-1), [WHITE, BG_GRAY]))
    t.setStyle(TableStyle(style))
    return t

# ── PAGE 1 — Combos + Rolls ──
def page1_story(data, cw, label=None):
    story = []
    date_str    = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct  = data.get('globalPct', 100)
    turno      = data.get('turno')
    production  = data.get('production', [])
    combos      = data.get('combos', [])

    total_rolls  = sum(r['qty'] for r in production)
    total_combos = sum(c['qty'] for c in combos)
    total_piezas = sum(r.get('piezas', r['qty']*14) for r in production)

    story.append(cover_band(date_str, global_pct, cw, label=label, turno=turno))
    story.append(Spacer(1, 4*mm))
    story.append(metrics_row(total_combos, total_rolls, total_piezas, global_pct, cw))
    story.append(Spacer(1, 4*mm))

    story.append(section_header("1  COMBOS A ARMAR", cw))
    story.append(Spacer(1, 2*mm))

    half = (len(combos)+1)//2
    left_col  = combos[:half]
    right_col = combos[half:]
    max_rows  = max(len(left_col), len(right_col)) if combos else 0

    combo_rows = [['Combo', 'Cant.', '', 'Combo', 'Cant.']]
    for i in range(max_rows):
        lc = left_col[i]  if i < len(left_col)  else {'name':'','qty':''}
        rc = right_col[i] if i < len(right_col) else {'name':'','qty':''}
        combo_rows.append([lc['name'], str(lc['qty']) if lc['qty'] else '',
                           '', rc['name'], str(rc['qty']) if rc['qty'] else ''])

    n_combos = len(combos)
    fs_c = 9 if n_combos <= 20 else 8 if n_combos <= 30 else 7
    gap = 3*mm
    col_name = (cw - gap) * 0.42
    col_qty  = (cw - gap) * 0.08
    ct = Table(combo_rows, colWidths=[col_name, col_qty, gap, col_name, col_qty], repeatRows=1)
    ct.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),fs_c),
        ('LEADING',(0,0),(-1,-1),fs_c+3), ('TOPPADDING',(0,0),(-1,-1),3),
        ('BOTTOMPADDING',(0,0),(-1,-1),3), ('LEFTPADDING',(0,0),(-1,-1),5),
        ('RIGHTPADDING',(0,0),(-1,-1),5),
        ('GRID',(0,0),(1,-1),0.3,colors.HexColor('#CCCCCC')),
        ('GRID',(3,0),(4,-1),0.3,colors.HexColor('#CCCCCC')),
        ('BACKGROUND',(0,0),(1,0),ACCENT), ('BACKGROUND',(3,0),(4,0),ACCENT),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(0,1),(1,-1),[WHITE,BG_GRAY]),
        ('ROWBACKGROUNDS',(3,1),(4,-1),[WHITE,BG_GRAY]),
        ('ALIGN',(1,0),(1,-1),'CENTER'), ('ALIGN',(4,0),(4,-1),'CENTER'),
        ('FONTNAME',(1,1),(1,-1),'Helvetica-Bold'), ('FONTNAME',(4,1),(4,-1),'Helvetica-Bold'),
    ]))
    story.append(ct)
    story.append(Spacer(1, 4*mm))

    story.append(section_header("2  ROLLOS A PRODUCIR", cw))
    story.append(Spacer(1, 2*mm))

    n_rolls = len(production)
    fs_r = 9 if n_rolls <= 18 else 8 if n_rolls <= 28 else 7
    half_r = (n_rolls+1)//2
    left_r  = production[:half_r]
    right_r = production[half_r:]
    max_r   = max(len(left_r), len(right_r)) if production else 0

    roll_rows = [['Roll', 'Rollos', 'Piezas', '', 'Roll', 'Rollos', 'Piezas']]
    for i in range(max_r):
        lr = left_r[i]  if i < len(left_r)  else {'name':'','qty':0}
        rr = right_r[i] if i < len(right_r) else {'name':'','qty':0}
        lr_piezas = lr.get('piezas', lr['qty']*14) if lr.get('qty') else ''
        rr_piezas = rr.get('piezas', rr['qty']*14) if rr.get('qty') else ''
        roll_rows.append([lr['name'], str(lr['qty']) if lr['qty'] else '', str(lr_piezas),
                          '', rr['name'], str(rr['qty']) if rr['qty'] else '', str(rr_piezas)])
    roll_rows.append(['TOTAL', str(total_rolls), str(total_piezas), '', '', '', ''])

    gap2   = 3*mm
    c_num  = 14*mm
    c_name = (cw - gap2 - c_num*4) / 2
    rt = Table(roll_rows, colWidths=[c_name, c_num, c_num, gap2, c_name, c_num, c_num], repeatRows=1)
    rt.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),fs_r),
        ('LEADING',(0,0),(-1,-1),fs_r+3), ('TOPPADDING',(0,0),(-1,-1),3),
        ('BOTTOMPADDING',(0,0),(-1,-1),3), ('LEFTPADDING',(0,0),(-1,-1),5),
        ('RIGHTPADDING',(0,0),(-1,-1),5),
        ('GRID',(0,0),(2,-1),0.3,colors.HexColor('#CCCCCC')),
        ('GRID',(4,0),(6,-1),0.3,colors.HexColor('#CCCCCC')),
        ('BACKGROUND',(0,0),(2,0),ACCENT), ('BACKGROUND',(4,0),(6,0),ACCENT),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(0,1),(2,-2),[WHITE,BG_GRAY]),
        ('ROWBACKGROUNDS',(4,1),(6,-2),[WHITE,BG_GRAY]),
        ('ALIGN',(1,0),(2,-1),'CENTER'), ('ALIGN',(5,0),(6,-1),'CENTER'),
        ('FONTNAME',(1,1),(2,-1),'Helvetica-Bold'), ('FONTNAME',(5,1),(6,-1),'Helvetica-Bold'),
        ('BACKGROUND',(0,-1),(2,-1),ACCENT), ('FONTNAME',(0,-1),(2,-1),'Helvetica-Bold'),
    ]))
    story.append(rt)

    rollos_blancos = data.get('rollosBlancos', [])
    if rollos_blancos:
        story.append(Spacer(1, 5*mm))
        story.append(section_header("3  ROLLOS BLANCOS (bases compartidas)", cw))
        story.append(Spacer(1, 2*mm))
        rb_rows = [['Grupo', 'Detalle', 'Total']]
        for g in rollos_blancos:
            detalle = ' + '.join(f"{r['name']} ({r['qty']})" for r in g['rolls'])
            rb_rows.append([g['grupo'], detalle, str(g['totalQty'])])
        col_grupo = 40*mm
        col_total = 25*mm
        col_detalle = cw - col_grupo - col_total
        rbt = Table(rb_rows, colWidths=[col_grupo, col_detalle, col_total], repeatRows=1)
        rbt.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),9),
            ('LEADING',(0,0),(-1,-1),12), ('TOPPADDING',(0,0),(-1,-1),4),
            ('BOTTOMPADDING',(0,0),(-1,-1),4), ('LEFTPADDING',(0,0),(-1,-1),5),
            ('RIGHTPADDING',(0,0),(-1,-1),5),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#CCCCCC')),
            ('BACKGROUND',(0,0),(-1,0),ACCENT), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),
            ('ALIGN',(2,0),(2,-1),'CENTER'), ('FONTNAME',(2,1),(2,-1),'Helvetica-Bold'),
            ('FONTSIZE',(2,1),(2,-1),12),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,BG_GRAY]),
        ]))
        story.append(rbt)

    return story

# ── PAGE 2 — Insumos + Semielaborados ──
def page2_story(data, cw, start_num=3, label=None):
    story = []
    date_str   = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct = data.get('globalPct', 100)
    turno      = data.get('turno')
    insumos    = data.get('insumos', {})
    semis      = data.get('semis', [])

    story.append(cover_band(date_str, global_pct, cw, label=label, turno=turno))
    story.append(Spacer(1, 5*mm))

    story.append(section_header(f"{start_num}  INSUMOS NECESARIOS", cw))
    story.append(Spacer(1, 2*mm))

    ins_rows = [['Insumo', 'En KG / unidad']]
    for k, v in insumos.items():
        ins_rows.append([v['label'], v['display']])

    it = _make_table(ins_rows, [110*mm, 75*mm], 11, ACCENT, True)
    story.append(it)

    story.append(PageBreak())
    story.append(cover_band(date_str, global_pct, cw, label=label, turno=turno))
    story.append(Spacer(1, 5*mm))

    story.append(section_header(f"{start_num+1}  SEMIELABORADOS", cw))
    story.append(Spacer(1, 2*mm))

    if semis:
        sem_rows = [['Semielaborado', 'Cantidad']]
        for s in semis:
            sem_rows.append([s['name'], s['display']])
        st = _make_table(sem_rows, [150*mm, 35*mm], 11, ACCENT, True)
        story.append(st)
    else:
        story.append(Paragraph("No hay semielaborados para esta produccion.",
                               S('x', fontSize=10, textColor=MED)))
    return story

# ── PAGE 3 — Grilla (LANDSCAPE) ──
def page3_story(data, cw, start_num=5):
    story = []
    date_str   = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct = data.get('globalPct', 100)
    turno      = data.get('turno')
    schedule   = data.get('schedule')
    if not schedule:
        return story

    story.append(cover_band(date_str, global_pct, cw, turno=turno))
    story.append(Spacer(1, 4*mm))
    story.append(section_header(f"{start_num}  GRILLA DE PRODUCCION POR HORA", cw))
    story.append(Spacer(1, 3*mm))

    info_data = [[
        Paragraph(f"Inicio: {schedule['startTime']}",          S('ib', fontName='Helvetica-Bold', fontSize=11)),
        Paragraph(f"Fin rolls: {schedule['rollsEndTime']}",    S('ib', fontName='Helvetica-Bold', fontSize=11)),
        Paragraph(f"Cierre total: {schedule['endTime']}",       S('ib', fontName='Helvetica-Bold', fontSize=11)),
    ]]
    info_t = Table(info_data, colWidths=[cw/3]*3)
    info_t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),BG_GRAY), ('BOX',(0,0),(-1,-1),0.4,colors.HexColor('#CCCCCC')),
        ('INNERGRID',(0,0),(-1,-1),0.4,colors.HexColor('#CCCCCC')),
        ('TOPPADDING',(0,0),(-1,-1),7), ('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('LEFTPADDING',(0,0),(-1,-1),10), ('ALIGN',(0,0),(-1,-1),'CENTER'),
    ]))
    story.append(info_t)
    story.append(Spacer(1, 4*mm))

    sushimanes = schedule.get('sushimanes', [])
    hours_data = schedule.get('hours', [])
    n_sm = len(sushimanes)
    if n_sm == 0:
        return story

    fs_g = 9 if n_sm <= 5 else 8 if n_sm <= 7 else 7
    hour_labels = [h['label'] for h in hours_data]

    hdr = ['Sushiman'] + hour_labels
    grid_rows = [hdr]
    for sm in sushimanes:
        row = [f"{sm['name']}\n{sm['prod']} r/h"]
        for h in hours_data:
            tasks = h['tasks'].get(str(sm['id']), [])
            cell  = '\n'.join(f"{t['name']}  x{t['qty']}" for t in tasks) if tasks else '—'
            row.append(cell)
        grid_rows.append(row)

    n_hours     = len(hours_data)
    sm_name_col = 28*mm
    hour_col_w  = (cw - sm_name_col) / n_hours if n_hours else 30*mm
    col_widths  = [sm_name_col] + [hour_col_w] * n_hours

    gt = Table(grid_rows, colWidths=col_widths, repeatRows=1)
    gt.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),fs_g),
        ('LEADING',(0,0),(-1,-1),fs_g+3), ('TOPPADDING',(0,0),(-1,-1),5),
        ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),5),
        ('RIGHTPADDING',(0,0),(-1,-1),5),
        ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#CCCCCC')),
        ('BACKGROUND',(0,0),(-1,0),BG_DARK), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,0),fs_g+1), ('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('BACKGROUND',(0,1),(0,-1),ACCENT), ('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),
        ('ROWBACKGROUNDS',(1,1),(-1,-1),[WHITE,BG_GRAY]),
        ('ALIGN',(0,0),(-1,-1),'CENTER'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
    ]))
    story.append(gt)

    semi_hourly = data.get('semiHourly', [])
    if semi_hourly:
        story.append(PageBreak())
        story.append(cover_band(date_str, global_pct, cw, turno=turno))
        story.append(Spacer(1, 4*mm))
        story.append(section_header(f"{start_num+1}  SEMIELABORADOS POR HORA", cw))
        story.append(Spacer(1, 2*mm))

        sh_hdr = ['Semielaborado'] + hour_labels + ['Total']
        sh_rows = [sh_hdr]
        for row in semi_hourly:
            r = [row['name']]
            for amt in row['byHour']:
                r.append(f"{round(amt,1)} {row['unit']}" if amt > 0 else '—')
            r.append(f"{round(row['total'],1)} {row['unit']}")
            sh_rows.append(r)

        name_col = 32*mm
        total_col = 22*mm
        sh_hour_col_w = (cw - name_col - total_col) / n_hours if n_hours else 30*mm
        sh_col_widths = [name_col] + [sh_hour_col_w]*n_hours + [total_col]

        sht = Table(sh_rows, colWidths=sh_col_widths, repeatRows=1)
        sht.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'), ('FONTSIZE',(0,0),(-1,-1),fs_g),
            ('LEADING',(0,0),(-1,-1),fs_g+3), ('TOPPADDING',(0,0),(-1,-1),5),
            ('BOTTOMPADDING',(0,0),(-1,-1),5), ('LEFTPADDING',(0,0),(-1,-1),5),
            ('RIGHTPADDING',(0,0),(-1,-1),5),
            ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#CCCCCC')),
            ('BACKGROUND',(0,0),(-1,0),ACCENT), ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('TEXTCOLOR',(0,0),(-1,0),WHITE),
            ('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),
            ('ROWBACKGROUNDS',(1,1),(-1,-1),[WHITE,BG_GRAY]),
            ('ALIGN',(0,0),(-1,-1),'CENTER'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('FONTNAME',(-1,1),(-1,-1),'Helvetica-Bold'),
        ]))
        story.append(sht)

    return story

# ── BUILD ──
def build_pdf(data: dict, out_path: str):
    from pypdf import PdfWriter, PdfReader
    import tempfile

    cw_p = A4_P[0] - 2*MARGIN
    cw_l = A4_L[0] - 2*MARGIN

    tmp_dir = tempfile.gettempdir()
    p1_path = os.path.join(tmp_dir, 'sushi_p1.pdf')
    p2_path = os.path.join(tmp_dir, 'sushi_p2.pdf')
    p3_path = os.path.join(tmp_dir, 'sushi_p3.pdf')
    p4_path = os.path.join(tmp_dir, 'sushi_p4.pdf')
    p5_path = os.path.join(tmp_dir, 'sushi_p5.pdf')

    def make_doc(path, pagesize):
        return SimpleDocTemplate(path, pagesize=pagesize,
            leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)

    has_rollos_blancos = bool(data.get('rollosBlancos'))
    offset = 4 if has_rollos_blancos else 3

    make_doc(p1_path, A4_P).build(page1_story(data, cw_p))
    make_doc(p2_path, A4_P).build(page2_story(data, cw_p, start_num=offset))
    make_doc(p3_path, A4_L).build(page3_story(data, cw_l, start_num=offset+2))

    paths = [p1_path, p2_path, p3_path]

    # Planilla 2 — producción restante para más tarde. No tiene grilla de horarios
    # (esa se arma en el momento, cuando corresponda producirla) — solo las listas
    # de referencia, igual que la planilla principal pero marcadas bien distintas.
    segunda = data.get('segundaPlanilla')
    if segunda:
        pct2 = segunda.get('pct', '')
        segunda = {**segunda, 'globalPct': pct2, 'turno': segunda.get('turno') or data.get('turno')}
        label2 = f"PLANILLA 2 — PRODUCCIÓN RESTANTE ({pct2}%) — PARA MÁS TARDE"
        offset2 = 4 if segunda.get('rollosBlancos') else 3
        make_doc(p4_path, A4_P).build(page1_story(segunda, cw_p, label=label2))
        make_doc(p5_path, A4_P).build(page2_story(segunda, cw_p, start_num=offset2, label=label2))
        paths += [p4_path, p5_path]

    writer = PdfWriter()
    for path in paths:
        if os.path.exists(path):
            for page in PdfReader(path).pages:
                writer.add_page(page)
    with open(out_path, 'wb') as f:
        writer.write(f)

    for p in paths:
        try: os.remove(p)
        except: pass

    print(f"PDF generado: {out_path}")


if __name__ == '__main__':
    data_path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/prod_data.json'
    out_path  = sys.argv[2] if len(sys.argv) > 2 else '/mnt/user-data/outputs/planilla_produccion.pdf'
    with open(data_path) as f:
        data = json.load(f)
    build_pdf(data, out_path)
