"""Share the explicitly isolated SQLite app; never connect to production."""
import json
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from types import SimpleNamespace
from unittest.mock import patch

import test_registration as isolated
import database
import reporting
from sales_service import correct_sale
from fastapi.testclient import TestClient


class SalesTests(unittest.TestCase):
    def setUp(self):
        with database.SessionLocal() as db:
            for model in (database.SaleCorrection, database.SaleSubmission, database.Sale, database.Member, database.ItemRegistration, database.Item):
                db.query(model).delete()
            item = database.Item(name='作品', price=90, creator_id='A123', material_fee=10)
            db.add(item)
            db.commit()
            self.item_id = item.id

    def payload(self, key=None, price=90):
        return dict(request_id=key or str(uuid.uuid4()), total_amount=price,
            items_json=json.dumps({'item':dict(item_id=self.item_id, price=price, quantity=1, creator_id='forged', name='<script>evil</script>')}))

    def post(self, payload):
        with TestClient(isolated.main.app) as client:
            return client.post('/sales', json=payload, auth=(isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD))

    def test_parallel_same_and_distinct_receipts(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: self.post(payload), range(6)))
        self.assertTrue(all(r.status_code == 200 for r in replies), [r.text for r in replies])
        self.assertEqual(len({r.json()['sale_id'] for r in replies}), 1)
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: self.post(self.payload()), range(6)))
        self.assertTrue(all(r.status_code == 200 for r in replies))
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Sale).count(), 7)
            info = json.loads(db.query(database.Sale).first().items_json)['item']
            self.assertEqual(info['creator_id'], 'A123')
            self.assertEqual(info['name'], '作品')

    def test_authentication_required_before_writing(self):
        payload = self.payload()
        with TestClient(isolated.main.app) as client:
            for headers in ({}, {'Authorization':'Basic invalid'}, {'Authorization':'Bearer fake'}):
                self.assertEqual(client.post('/sales',json=payload,headers=headers).status_code,401)
            self.assertEqual(client.post('/sales',json=payload,auth=('wrong','wrong')).status_code,401)
            self.assertEqual(client.get('/').status_code,401)
            self.assertEqual(client.get('/',auth=(isolated.main.ADMIN_USERNAME,isolated.main.ADMIN_PASSWORD)).status_code,200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Sale).count(),0)
            self.assertEqual(db.query(database.SaleSubmission).count(),0)
        self.assertEqual(self.post(payload).status_code,200)

    def test_stale_price_deleted_and_manual_rejected(self):
        self.assertEqual(self.post(self.payload(price=100)).status_code, 409)
        payload = self.payload()
        info = json.loads(payload['items_json'])
        info['item']['item_id'] = None
        payload['items_json'] = json.dumps(info)
        self.assertEqual(self.post(payload).status_code, 422)
        with database.SessionLocal() as db:
            db.query(database.Item).delete()
            db.commit()
        self.assertEqual(self.post(self.payload()).status_code, 409)

    def test_register_account_can_checkout_but_cannot_administer(self):
        auth = (isolated.main.REGISTER_USERNAME, isolated.main.REGISTER_PASSWORD)
        with TestClient(isolated.main.app) as client:
            self.assertEqual(client.get('/',auth=auth).status_code,200)
            payload = self.payload()
            first = client.post('/sales',json=payload,auth=auth)
            self.assertEqual(first.status_code,200)
            self.assertEqual(client.post('/sales',json=payload,auth=auth).json(),first.json())
            for path in ('/admin','/admin/items','/members','/sales','/reports/summary','/export/overall','/export/distribution'):
                self.assertEqual(client.get(path,auth=auth).status_code,401,path)
            self.assertEqual(client.delete('/items/'+str(self.item_id),auth=auth).status_code,401)
            self.assertEqual(client.post('/members',json={'student_id':'A123','shift_slots':1},auth=auth).status_code,401)
            self.assertEqual(client.put('/items/'+str(self.item_id),data={'name':'changed','price':1,'creator_id':'A123'},auth=auth).status_code,401)

    def test_public_catalog_privacy_and_server_side_author(self):
        auth = (isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD)
        with TestClient(isolated.main.app) as client:
            public = client.get('/items')
            self.assertEqual(public.headers['cache-control'], 'no-store')
            item = public.json()[0]
            self.assertEqual(set(item), {'id','name','price','category','image_url','sale_price','is_sale'})
            self.assertNotIn('A123', public.text)
            self.assertEqual(client.get('/admin/items').status_code, 401)
            self.assertEqual(client.get('/admin/items',auth=('invalid','invalid')).status_code, 401)
            admin = client.get('/admin/items', auth=auth)
            self.assertEqual(admin.json()[0]['creator_id'], 'A123')
            self.assertEqual(admin.headers['cache-control'], 'private, no-store')
            data = self.payload()
            data['items_json'] = json.dumps({'a':dict(item_id=item['id'],price=item['price'],quantity=1)})
            self.assertEqual(client.post('/sales',json=data,auth=auth).status_code,200)
            sale = client.get('/sales',auth=auth).json()[0]
            saved = json.loads(sale['items_json'])['a']
            self.assertEqual(saved['creator_id'],'A123')
            self.assertEqual(saved['material_fee'],10)
            summary = client.get('/reports/summary',auth=auth).json()
            self.assertEqual(summary['people'][0]['author'],63)

    def test_retry_saved_sale_after_catalog_changes(self):
        payload = self.payload()
        first = self.post(payload)
        with database.SessionLocal() as db:
            db.get(database.Item, self.item_id).price = 999
            db.commit()
        self.assertEqual(self.post(payload).json(), first.json())
        altered = self.payload(payload['request_id'], price=999)
        self.assertEqual(self.post(altered).status_code, 409)

    def test_commit_failure_rolls_back_receipt(self):
        payload = self.payload()
        with patch('sqlalchemy.orm.Session.commit', side_effect=RuntimeError('simulated failure')):
            self.assertEqual(self.post(payload).status_code, 503)
        self.assertEqual(self.post(payload).status_code, 200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Sale).count(), 1)

    def test_jst_bounds_and_correction_history(self):
        with database.SessionLocal() as db:
            for timestamp in [datetime(2026,10,25,14,59,59), datetime(2026,10,25,15), datetime(2026,10,26,14,59,59), datetime(2026,10,26,15)]:
                db.add(database.Sale(total_amount=90, items_json='{}', created_at=timestamp))
            db.commit()
            chosen = reporting.selected_sales(db, date(2026,10,26), date(2026,10,26))
            self.assertEqual(len(chosen), 2)
            sid = chosen[0]['id']
            cid = correct_sale(db, sid, '重複入力', 'developer', 0)
            db.commit()
            corrected = reporting.selected_sales(db, date(2026,10,26), date(2026,10,26))
            self.assertTrue(corrected[0]['voided'])
            self.assertEqual(corrected[0]['corrections'][0]['reason'], '重複入力')
            self.assertEqual(db.get(database.Sale, sid).total_amount, 90)
            with self.assertRaises(ValueError):
                correct_sale(db, sid, 'stale correction', 'developer', 0)
            db.rollback()
            replacement = isolated.main.SaleCreate(**self.payload())
            correct_sale(db, sid, '取消の訂正', 'developer', cid, replacement)
            db.commit()
            result = reporting.selected_sales(db, date(2026,10,26), date(2026,10,26))[0]
            self.assertFalse(result['voided'])
            self.assertEqual(len(result['corrections']), 2)
            self.assertEqual(db.get(database.Sale, sid).total_amount, 90)

    def test_report_api_and_csv(self):
        self.post(self.payload())
        auth = (isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD)
        with TestClient(isolated.main.app) as client:
            self.assertEqual(client.get('/reports/summary').status_code, 401)
            report = client.get('/reports/summary', auth=auth).json()
            self.assertEqual(report['people'][0]['author'], 63)
            self.assertEqual(report['unallocated'], 0)
            self.assertEqual(report['club_adjustment'], 18)
            self.assertEqual(report['club_fee'], 27)
            for route in ('overall', 'distribution'):
                response = client.get('/export/' + route, auth=auth)
                self.assertEqual(response.status_code, 200)
                self.assertIn('未配分残額', response.text)
            self.assertEqual(client.get('/sales?start=2026-10-27&end=2026-10-26', auth=auth).status_code, 422)

    def test_forty_products_two_hundred_sales(self):
        with database.SessionLocal() as db:
            db.query(database.Item).delete()
            for n in range(40):
                db.add(database.Item(name=f'商品{n}', price=1000, creator_id=f'A{n}', material_fee=100))
                db.add(database.Member(student_id=f'A{n}', shift_slots=1))
            db.commit()
            ids = [i.id for i in db.query(database.Item).order_by(database.Item.id)]
        with TestClient(isolated.main.app) as client:
            for n in range(200):
                self.item_id = ids[n % 40]
                self.assertEqual(client.post('/sales', json=self.payload(price=1000), auth=(isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD)).status_code, 200)
            self.assertEqual(len(client.get('/items').json()), 40)
            report = client.get('/reports/summary', auth=(isolated.main.ADMIN_USERNAME, isolated.main.ADMIN_PASSWORD)).json()
            self.assertEqual(report['count'], 200)
            self.assertEqual(report['revenue'], 200000)
            self.assertEqual(sum(p['author'] for p in report['people']), 140000)
            self.assertEqual(sum(p['shift'] for p in report['people']), 40000)
            self.assertEqual(report['group'], 20000)
            self.assertEqual(report['unallocated'], 0)


class CalculationTests(unittest.TestCase):
    def sales(self, prices):
        return [dict(total_amount=p, items_json=json.dumps({'a':dict(price=p,quantity=1,creator_id='A')}), voided=False) for p in prices]

    def test_exact_integer_and_per_person_rounding(self):
        r = reporting.calculate(self.sales([90]), [])
        self.assertEqual(r['people'][0]['author'], 63)
        r = reporting.calculate(self.sales([101,101,101]), [SimpleNamespace(student_id='A',shift_slots=1),SimpleNamespace(student_id='B',shift_slots=2)])
        self.assertEqual(r['people'][0]['author'], 212)
        self.assertEqual([p['shift'] for p in r['people']], [20,40])
        self.assertEqual(r['unallocated'], 0)
        self.assertEqual(r['club_adjustment'], 1)
        self.assertEqual(r['revenue'], sum(p['total'] for p in r['people']) + r['club_fee'])

    def test_legacy_manual_unallocated(self):
        sales = self.sales([100])
        sales[0]['items_json'] = sales[0]['items_json'].replace('"A"', '"manual"')
        r = reporting.calculate(sales, [])
        self.assertEqual(r['unallocated'], 0)
        self.assertEqual(r['club_fee'], 100)

    def test_zero_sales_club_fee(self):
        r = reporting.calculate([], [])
        self.assertEqual(r['club_fee'], 0)
