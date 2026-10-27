import csv
import io
import json
import unittest
from datetime import datetime

import test_registration as isolated
import database
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
            for route in ('/export/period'+dates,'/reports/dashboard'+dates):
                self.assertEqual(c.get(route).status_code, 401)
            d = c.get('/reports/dashboard'+dates, auth=self.auth).json()
            self.assertEqual(d['overall']['revenue'], 202)
            self.assertEqual(d['period']['revenue'], 101)
            for kind in ('overall','distribution'):
                self.assertEqual(c.get('/export/'+kind, auth=self.auth).content,
                                 c.get('/export/'+kind+dates, auth=self.auth).content)
            response = c.get('/export/period'+dates, auth=self.auth)
            rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
            self.assertIn(['総売上','101'], rows)
            self.assertNotIn('未配分残額', response.text)
            for route in ('/export/period','/reports/dashboard'):
                self.assertEqual(c.get(route+'?start=2026-10-27&end=2026-10-26',auth=self.auth).status_code,422)
