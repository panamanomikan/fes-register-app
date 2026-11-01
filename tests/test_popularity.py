"""Uses only the isolated SQLite database configured by test_registration."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import event
from fastapi.testclient import TestClient
import test_registration as isolated
import database
import popularity


class PopularityTests(unittest.TestCase):
    def test_ranking_is_read_only_authenticated_and_uses_effective_quantities(self):
        now = datetime(2026, 11, 2, 4)
        with database.SessionLocal() as db:
            for model in (database.SaleCorrection, database.SaleSubmission, database.Sale,
                          database.ItemRegistration, database.Item):
                db.query(model).delete()
            items = [database.Item(name='同名', price=500, creator_id='private') for _ in range(13)]
            db.add_all(items)
            db.flush()
            ids = [item.id for item in items]
            items[-1].deleted_at = now
            def sale(index, quantity, minutes):
                details = json.dumps({'line': {'item_id': ids[index], 'quantity': quantity, 'price': 1}})
                obj = database.Sale(total_amount=quantity, items_json=details, created_at=now-timedelta(minutes=minutes))
                db.add(obj)
                db.flush()
                return obj
            for index in range(13):
                sale(index, index+1, 30)
            sale(0, 999, 61)
            sale(0, 999, -1)
            boundary = sale(0, 100, 60)
            void = sale(1, 999, 1)
            for obj, qty in ((boundary, 20), (void, 0)):
                db.add(database.SaleCorrection(sale_id=obj.id, before_total=obj.total_amount,
                    before_items=obj.items_json, after_total=qty,
                    after_items=json.dumps({'line': {'item_id':ids[0], 'quantity':qty}}) if qty else '{}',
                    reason='test', corrected_by='test'))
            db.commit()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.strip().split()[0].upper())
        event.listen(database.engine, 'before_cursor_execute', capture)
        try:
            with TestClient(isolated.main.app) as client:
                self.assertEqual(client.get('/items/popular').status_code, 401)
                with patch('popularity.datetime') as clock:
                    clock.utcnow.return_value = now
                    result = client.get('/items/popular', auth=(isolated.main.REGISTER_USERNAME, isolated.main.REGISTER_PASSWORD))
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), {'item_ids': [ids[0]] + list(reversed(ids[3:12]))})
                self.assertEqual(result.headers['cache-control'], 'private, no-store')
            self.assertTrue(statements)
            self.assertEqual(set(statements), {'SELECT'})
        finally:
            event.remove(database.engine, 'before_cursor_execute', capture)
