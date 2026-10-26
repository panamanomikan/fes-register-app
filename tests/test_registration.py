"""Isolated API tests. No production DB or Storage requests are made."""
import os
import atexit
import tempfile
import unittest
import uuid
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

# Set before importing the app; dotenv never overrides these test settings.
scratch = tempfile.TemporaryDirectory(prefix='registration-test-', dir=Path('tests').resolve())
os.environ['DATABASE_URL'] = 'sqlite:///' + scratch.name.replace('\\', '/') + '/test.db'
os.environ['SUPABASE_URL'] = 'https://test.invalid'
os.environ['SUPABASE_ANON_KEY'] = 'test-only'
storage = MagicMock()
storage.storage.from_.return_value.get_public_url.side_effect = lambda path: 'https://test.invalid/' + path
with patch('supabase.create_client', return_value=storage):
    import main
import database
from fastapi.testclient import TestClient


def cleanup():
    database.engine.dispose()
    scratch.cleanup()


atexit.register(cleanup)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        with database.SessionLocal() as db:
            db.query(database.ItemRegistration).delete()
            db.query(database.Item).delete()
            db.commit()
        storage.reset_mock()
        storage.storage.from_.return_value.upload.side_effect = None

    def register(self, key, name='test'):
        with TestClient(main.app) as client:
            return client.post('/items', data={'name': name, 'price': '100', 'material_fee': '150', 'creator_id': 'A123', 'request_id': key}, files={'file': ('test.jpg', b'image', 'image/jpeg')})

    def test_parallel_same_request_saved_once(self):
        key = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: self.register(key), range(6)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(len({r.json()['item_id'] for r in responses}), 1)
        self.assertEqual(storage.storage.from_.return_value.upload.call_count, 1)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Item).count(), 1)
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/item-registrations/' + key).status_code, 200)
        self.assertEqual(self.register(key, 'changed').status_code, 409)

    def test_parallel_distinct_requests(self):
        keys = [str(uuid.uuid4()) for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(self.register, keys))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(len({r.json()['item_id'] for r in responses}), 6)

    def test_upload_failure_can_retry_same_id(self):
        key = str(uuid.uuid4())
        storage.storage.from_.return_value.upload.side_effect = RuntimeError('simulated upload failure')
        self.assertEqual(self.register(key).status_code, 503)
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/item-registrations/' + key).status_code, 404)
        storage.storage.from_.return_value.upload.side_effect = None
        self.assertEqual(self.register(key).status_code, 200)
        self.assertEqual(self.register(key).status_code, 200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Item).count(), 1)

    def test_receipt_and_item_roll_back_together(self):
        key = str(uuid.uuid4())
        with patch('sqlalchemy.orm.Session.commit', side_effect=RuntimeError('commit failed')):
            self.assertEqual(self.register(key).status_code, 503)
        self.assertEqual(self.register(key).status_code, 200)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.Item).count(), 1)

    def test_slow_upload_does_not_block_other_requests(self):
        entered, release = Event(), Event()
        def slow_upload(**kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError('test upload not released')
        storage.storage.from_.return_value.upload.side_effect = slow_upload
        with ThreadPoolExecutor(max_workers=2) as pool:
            uploading = pool.submit(self.register, str(uuid.uuid4()))
            try:
                self.assertTrue(entered.wait(5))
                with TestClient(main.app) as client:
                    response = client.get('/items')
                self.assertEqual(response.status_code, 200)
                self.assertFalse(uploading.done())
            finally:
                release.set()
            self.assertEqual(uploading.result(timeout=5).status_code, 200)


if __name__ == '__main__':
    unittest.main()
