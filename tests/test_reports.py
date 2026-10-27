import csv
import io
import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from zipfile import ZipFile

import test_registration as isolated
import database
import excel_report
from fastapi.testclient import TestClient


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.auth = (isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD)
        with database.SessionLocal() as db:
            for model in (database.SaleCorrection, database.SaleSubmission, database.Sale, database.Member):
                db.query(model).delete()
            for timestamp in (datetime(2026,10,25,15), datetime(2026,10,26,15)):
                db.add(database.Sale(total_amount=101, created_at=timestamp, items_json=json.dumps({
                    '1':dict(name='=1+1',price=101,quantity=1,creator_id='001',material_fee=10)})))
            db.add(database.Member(student_id='001', shift_slots=1))
            db.add(database.Member(student_id='002', shift_slots=2))
            db.commit()

    def test_scope_and_protection(self):
        with TestClient(isolated.main.app) as c:
            dates = '?start=2026-10-26&end=2026-10-26'
            for route in ('/export/excel','/export/period'+dates,'/reports/dashboard'+dates):
                self.assertEqual(c.get(route).status_code, 401)
            d = c.get('/reports/dashboard'+dates, auth=self.auth).json()
            self.assertEqual(d['overall']['revenue'], 202)
            self.assertEqual(d['period']['revenue'], 101)
            for kind in ('overall','distribution','excel'):
                self.assertEqual(c.get('/export/'+kind, auth=self.auth).content,
                                 c.get('/export/'+kind+dates, auth=self.auth).content)
            response = c.get('/export/period'+dates, auth=self.auth)
            rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
            self.assertIn(['総売上','101'], rows)
            self.assertNotIn('未配分残額', response.text)
            for route in ('/export/period','/reports/dashboard'):
                self.assertEqual(c.get(route+'?start=2026-10-27&end=2026-10-26',auth=self.auth).status_code,422)

    def test_excel_formulas_text_and_cached_totals(self):
        with TestClient(isolated.main.app) as c:
            response = c.get('/export/excel', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        with ZipFile(io.BytesIO(response.content)) as book:
            ns = {'s':excel_report.NS}
            def cells(path):
                return {c.get('r'):c for c in ET.fromstring(book.read(path)).findall('.//s:c',ns)}
            p = cells('xl/worksheets/sheet1.xml')
            d = cells('xl/worksheets/sheet2.xml')
            self.assertEqual(p['B11'].get('t'), 'inlineStr')
            self.assertIsNone(p['B11'].find('s:f',ns))
            self.assertEqual(p['A11'].find('s:is/s:t',ns).text, '001')
            self.assertEqual(p['H11'].find('s:f',ns).text, 'C11*G11')
            self.assertEqual(d['B4'].find('s:v',ns).text, '202')
            self.assertEqual(d['B5'].find('s:v',ns).text, '20')
            self.assertEqual(d['B6'].find('s:v',ns).text, '180')
            self.assertEqual(d['B7'].find('s:v',ns).text, '2')
            self.assertEqual(d['D11'].find('s:v',ns).text, '141')
            self.assertIn('QUOTIENT', d['E11'].find('s:f',ns).text)

    def test_empty_excel(self):
        with database.SessionLocal() as db:
            db.query(database.Sale).delete()
            db.query(database.Member).delete()
            db.commit()
        with TestClient(isolated.main.app) as c:
            response = c.get('/export/excel',auth=self.auth)
        with ZipFile(io.BytesIO(response.content)) as book:
            root = ET.fromstring(book.read('xl/worksheets/sheet2.xml'))
            cell = root.find(".//"+excel_report.tag('c')+"[@r='B7']/"+excel_report.tag('v'))
            self.assertEqual(cell.text, '0')
