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

def cover_band(date_str, global_pct, width):
    title_style = S('dt', fontName='Helvetica-Bold', fontSize=18,
                    textColor=WHITE, leading=22)
    sub_style   = S('ds', fontSize=10, textColor=colors.HexColor('#DDDDDD'),
                    alignment=TA_RIGHT, leading=14)

    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'logo.png')
    logo_h = 30
    logo_w = logo_h * (364/170)

    if os.path.exists(logo_path):
        logo_cell = RLImage(logo_path, width=logo_w, height=logo_h)
        left_col_w = logo_w + 16
    else:
        logo_cell = Paragraph("SUSHI", title_style)
        left_col_w = width * 0.25

    right_w = width - left_col_w - 4*mm
    t = Table([[ logo_cell,
                 Paragraph("PLANILLA DE PRODUCCION", title_style),
                 Paragraph(f"{date_str}  |  {global_pct}% de la venta", sub_style) ]],
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
    return t

def metrics_row(combos_total, rolls_total, piezas_total, global_pct, width):
    items = [
        ("COMBOS",  str(combos_total), "unidades"),
        ("ROLLOS",  str(rolls_total),  "total"),
        ("PIEZAS",  str(piezas_total), "14 x rollo"),
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
def page1_story(data, cw):
    story = []
    date_str    = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct  = data.get('globalPct', 100)
    production  = data.get('production', [])
    combos      = data.get('combos', [])

    total_rolls  = sum(r['qty'] for r in production)
    total_combos = sum(c['qty'] for c in combos)
    total_piezas = total_rolls * 14

    story.append(cover_band(date_str, global_pct, cw))
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
        roll_rows.append([lr['name'], str(lr['qty']) if lr['qty'] else '', str(lr['qty']*14) if lr['qty'] else '',
                          '', rr['name'], str(rr['qty']) if rr['qty'] else '', str(rr['qty']*14) if rr['qty'] else ''])
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
    return story

# ── PAGE 2 — Insumos + Semielaborados ──
def page2_story(data, cw):
    story = []
    date_str   = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct = data.get('globalPct', 100)
    insumos    = data.get('insumos', {})
    semis      = data.get('semis', [])

    story.append(cover_band(date_str, global_pct, cw))
    story.append(Spacer(1, 5*mm))

    story.append(section_header("3  INSUMOS NECESARIOS", cw))
    story.append(Spacer(1, 2*mm))

    ins_rows = [['Insumo', 'Total', 'En KG / unidad']]
    for k, v in insumos.items():
        ins_rows.append([v['label'], str(v['total']), v['display']])

    it = _make_table(ins_rows, [110*mm, 35*mm, 40*mm], 11, ACCENT, True)
    story.append(it)
    story.append(Spacer(1, 6*mm))

    story.append(section_header("4  SEMIELABORADOS", cw))
    story.append(Spacer(1, 2*mm))

    if semis:
        sem_rows = [['Semielaborado', 'Rolls que lo usan', 'Cantidad']]
        for s in semis:
            sem_rows.append([s['name'], s['usedIn'], s['display']])
        st = _make_table(sem_rows, [68*mm, 82*mm, 35*mm], 11, ACCENT, True)
        story.append(st)
    else:
        story.append(Paragraph("No hay semielaborados para esta produccion.",
                               S('x', fontSize=10, textColor=MED)))
    return story

# ── PAGE 3 — Grilla (LANDSCAPE) ──
def page3_story(data, cw):
    story = []
    date_str   = data.get('date', datetime.now().strftime('%d/%m/%Y'))
    global_pct = data.get('globalPct', 100)
    schedule   = data.get('schedule')
    if not schedule:
        return story

    story.append(cover_band(date_str, global_pct, cw))
    story.append(Spacer(1, 4*mm))
    story.append(section_header("5  GRILLA DE PRODUCCION POR HORA", cw))
    story.append(Spacer(1, 3*mm))

    info_data = [[
        Paragraph(f"Inicio: {schedule['startTime']}",          S('ib', fontName='Helvetica-Bold', fontSize=11)),
        Paragraph(f"Fin rolls: {schedule['rollsEndTime']}",    S('ib', fontName='Helvetica-Bold', fontSize=11)),
        Paragraph(f"Corte y armado: {schedule['cutMins']} min",S('ib', fontName='Helvetica-Bold', fontSize=11)),
        Paragraph(f"Fin total: {schedule['endTime']}",         S('ib', fontName='Helvetica-Bold', fontSize=11)),
    ]]
    info_t = Table(info_data, colWidths=[cw/4]*4)
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
    cut_label   = f"Corte\n+armado\n{schedule['cutMins']}min"

    hdr = ['Sushiman'] + hour_labels + [cut_label]
    grid_rows = [hdr]
    for sm in sushimanes:
        row = [f"{sm['name']}\n{sm['prod']} r/h"]
        for h in hours_data:
            tasks = h['tasks'].get(str(sm['id']), [])
            cell  = '\n'.join(f"{t['name']}  x{t['qty']}" for t in tasks) if tasks else '—'
            row.append(cell)
        row.append('')
        grid_rows.append(row)

    n_hours     = len(hours_data)
    sm_name_col = 28*mm
    cut_col     = 22*mm
    hour_col_w  = (cw - sm_name_col - cut_col) / n_hours if n_hours else 30*mm
    col_widths  = [sm_name_col] + [hour_col_w] * n_hours + [cut_col]

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
        ('ROWBACKGROUNDS',(1,1),(-2,-1),[WHITE,BG_GRAY]),
        ('ALIGN',(0,0),(-1,-1),'CENTER'), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('BACKGROUND',(-1,0),(-1,-1),WARN_BG), ('FONTNAME',(-1,0),(-1,-1),'Helvetica-Bold'),
        ('TEXTCOLOR',(-1,1),(-1,-1),colors.HexColor('#856404')), ('SPAN',(-1,1),(-1,-1)),
    ]))
    story.append(gt)
    return story

# ── BUILD ──
def build_pdf(data: dict, out_path: str):
    from pypdf import PdfWriter, PdfReader

    cw_p = A4_P[0] - 2*MARGIN
    cw_l = A4_L[0] - 2*MARGIN

    tmp_dir = tempfile_dir = os.environ.get('TMPDIR', '/tmp')
    import tempfile
    tmp_dir = tempfile.gettempdir()
    p1_path = os.path.join(tmp_dir, 'sushi_p1.pdf')
    p2_path = os.path.join(tmp_dir, 'sushi_p2.pdf')
    p3_path = os.path.join(tmp_dir, 'sushi_p3.pdf')

    def make_doc(path, pagesize):
        return SimpleDocTemplate(path, pagesize=pagesize,
            leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)

    make_doc(p1_path, A4_P).build(page1_story(data, cw_p))
    make_doc(p2_path, A4_P).build(page2_story(data, cw_p))
    make_doc(p3_path, A4_L).build(page3_story(data, cw_l))

    writer = PdfWriter()
    for path in [p1_path, p2_path, p3_path]:
        if os.path.exists(path):
            for page in PdfReader(path).pages:
                writer.add_page(page)
    with open(out_path, 'wb') as f:
        writer.write(f)

    for p in [p1_path, p2_path, p3_path]:
        try: os.remove(p)
        except: pass

    print(f"PDF generado: {out_path}")


if __name__ == '__main__':
    data_path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/prod_data.json'
    out_path  = sys.argv[2] if len(sys.argv) > 2 else '/mnt/user-data/outputs/planilla_produccion.pdf'
    with open(data_path) as f:
        data = json.load(f)
    build_pdf(data, out_path)
