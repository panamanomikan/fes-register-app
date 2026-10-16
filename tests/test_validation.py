"""Validate request rules without importing the app or touching the production DB."""
import ast
import json
from pathlib import Path
import unittest

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

source = ast.parse(Path('main.py').read_text(encoding='utf-8'))
definitions = ast.Module(body=[node for node in source.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in {'SaleCreate', 'MemberUpdate', 'validate_item'}], type_ignores=[])
scope = dict(json=json, BaseModel=BaseModel, Field=Field, model_validator=model_validator, HTTPException=HTTPException)
exec(compile(definitions, 'main.py', 'exec'), scope)


class ValidationTests(unittest.TestCase):
    def test_normal_and_sale_details(self):
        details = {'normal': {'price': 500, 'quantity': 2}, 'sale': {'price': 300, 'quantity': 1, 'original_price': 500, 'is_sale': True}}
        sale = scope['SaleCreate'](total_amount=1300, items_json=json.dumps(details))
        self.assertEqual(sale.total_amount, 1300)

    def test_invalid_sales(self):
        for details, total in [('broken', 100), ('{}', 100), ('[]', 100), ('{"a": 1}', 100), ('{"a":{"price":-1,"quantity":1}}', 100), ('{"a":{"price":100,"quantity":0}}', 100), ('{"a":{"price":100,"quantity":1}}', 200)]:
            with self.subTest(details=details), self.assertRaises(ValidationError):
                scope['SaleCreate'](total_amount=total, items_json=details)

    def test_item_validation(self):
        scope['validate_item']('商品', 500, 100, 0, True)
        for args in [(' ', 100, 0), ('商品', -1, 0), ('商品', 100, -1), ('商品', 100, 0, None, True)]:
            with self.assertRaises(HTTPException):
                scope['validate_item'](*args)

    def test_negative_shift_rejected(self):
        with self.assertRaises(ValidationError):
            scope['MemberUpdate'](student_id='test', shift_slots=-1)


if __name__ == '__main__':
    unittest.main()
