"""Fill the artifact-authored XLSX template with an export-time snapshot.

Only server-generated formulas are written as formulas. User text is inline text.
Cached results use the same integer arithmetic as CSV; Excel recalculates on open.
"""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import re
import xml.etree.ElementTree as ET

from fastapi.responses import Response
import database
import reporting

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', NS)
def tag(name):
    return '{' + NS + '}' + name


def sheet_xml(template, rows):
    root = ET.fromstring(template)
    data = root.find(tag('sheetData'))
    styles = {c.get('r'): c.get('s', '0') for c in data.iter(tag('c'))}
    data.clear()
    for n, values in sorted(rows.items()):
        row = ET.SubElement(data, tag('row'), r=str(n), ht='28' if n != 10 else '34', customHeight='1')
        for col, value in enumerate(values):
            if value is None:
                continue
            letter = chr(65 + col)
            style = styles.get(f'{letter}{n if n <= 10 else 11}', '0')
            cell = ET.SubElement(row, tag('c'), r=f'{letter}{n}', s=style)
            if isinstance(value, tuple):
                formula, cached = value
                ET.SubElement(cell, tag('f')).text = formula
                ET.SubElement(cell, tag('v')).text = str(cached)
            elif isinstance(value, (int, float)):
                ET.SubElement(cell, tag('v')).text = str(value)
            else:
                cell.set('t', 'inlineStr')
                text = ET.SubElement(ET.SubElement(cell, tag('is')), tag('t'))
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                text.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]
    dimension = root.find(tag('dimension'))
    if dimension is not None:
        dimension.set('ref', f'A1:H{max(rows)}')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def workbook_bytes(report, label):
    products = list(report['products'].items())
    end = max(11, 10 + len(products))
    people_end = max(11, 10 + len(report['people']))
    detail_total = sum(k[2] * q for k, q in products)
    product_rows = {
        2: ['商品別売上'], 3: ['対象期間 (JST)', label],
        4: ['総売上（円）', (f'SUM(H11:H{end})+B5', report['revenue'])],
        5: ['内訳未設定売上（円）', report['revenue'] - detail_total],
        6: ['会計件数', report['count']],
        7: ['材料費は参考情報。売上から差し引きません。'],
        8: ['登録時点ではなく、会計時点の単価・作者で集計します。'],
        10: ['製作者学籍番号','作品名','販売単価（円）','材料費（円）','備考','セール','個数','売上（円）'],
    }
    by_author = {}
    for row, (key, qty) in enumerate(products, 11):
        cid, name, price, material, remarks, sale = key
        product_rows[row] = [cid, name, price, material, remarks, 'あり' if sale else 'なし', qty, (f'C{row}*G{row}', price*qty)]
        by_author.setdefault(cid, []).append(row)
    paid = sum(p['total'] for p in report['people'])
    person_rows = {
        2: ['個人分配'], 3: ['対象期間 (JST)', label],
        4: ['総売上（円）', ("'商品別売上'!B4", report['revenue'])],
        5: ['団体還元10％（円）', ('QUOTIENT(B4*10,100)', report['group'])],
        6: ['個人支払合計（円）', (f'SUM(F11:F{people_end})', paid)],
        7: ['未配分残額（円）', ('B4-B5-B6', report['unallocated'])],
        8: ['シフト総コマ数', (f'SUM(B11:B{people_end})', report['total_slots'])],
        9: ['個人ごとに円未満切捨て。作者不明・旧手動入力は支払対象外。'],
        10: ['学籍番号','シフトコマ数','作者売上（円）','作者70％（円）','シフト20％（円）','支払合計（円）','支払対象(1=可)'],
    }
    for row, p in enumerate(report['people'], 11):
        cid = p['student_id']
        refs = by_author.get(cid, [])
        author_sum = sum(products[r-11][0][2] * products[r-11][1] for r in refs)
        # Explicit source rows preserve case and leading zeros in student IDs.
        formula = '+'.join(f"'商品別売上'!H{r}" for r in refs) or '0'
        person_rows[row] = [cid, p['shift_slots'], (formula, author_sum),
            (f'IF(G{row}=1,QUOTIENT(C{row}*70,100),0)', p['author']),
            (f'IF(AND(G{row}=1,$B$8>0),QUOTIENT($B$4*20*B{row},100*$B$8),0)', p['shift']),
            (f'D{row}+E{row}', p['total']), int(cid not in ('manual','unknown'))]
    output = BytesIO()
    with ZipFile(Path(__file__).parent / 'report_assets/report_template.xlsx') as src, ZipFile(output, 'w', ZIP_DEFLATED) as dst:
        for entry in src.infolist():
            data = src.read(entry.filename)
            if entry.filename == 'xl/worksheets/sheet1.xml':
                data = sheet_xml(data, product_rows)
            elif entry.filename == 'xl/worksheets/sheet2.xml':
                data = sheet_xml(data, person_rows)
            elif entry.filename == 'xl/workbook.xml':
                root = ET.fromstring(data)
                calc = root.find(tag('calcPr'))
                if calc is None:
                    calc = ET.SubElement(root, tag('calcPr'))
                calc.set('fullCalcOnLoad', '1')
                calc.set('calcMode', 'auto')
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            dst.writestr(entry, data)
    return output.getvalue()


def export(db):
    report = reporting.calculate(reporting.selected_sales(db), db.query(database.Member).all())
    return Response(workbook_bytes(report, '全期間'),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':'attachment; filename=overall_report.xlsx', 'Cache-Control':'no-store'})
