"""Developer console only. Preview by default; requires explicit --apply."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sale_id', type=int)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--expected-revision', type=int, required=True, help='0 if never corrected; otherwise latest correction id')
    parser.add_argument('--replacement', type=Path, help='JSON containing total_amount and items_json. Omit to void.')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    # Import only after parsing: --help never connects to the database.
    import uuid
    import database
    from main import SaleCreate
    from sales_service import correct_sale
    from reporting import selected_sales
    replacement = None
    if args.replacement:
        content = json.loads(args.replacement.read_text(encoding='utf-8'))
        content['request_id'] = str(uuid.uuid4())
        replacement = SaleCreate(**content)
    with database.SessionLocal() as db:
        original = next((s for s in selected_sales(db) if s['id'] == args.sale_id), None)
        if not original:
            raise ValueError('取引がありません')
        print(json.dumps({'before': original, 'after': replacement.model_dump(mode='json') if replacement else '取消',
            'reason': args.reason, 'operator': args.operator}, ensure_ascii=False, indent=2, default=str))
        if not args.apply:
            print('プレビューのみ。内容確認後に --apply を付けて実行してください。')
            return
        correction_id = correct_sale(db, args.sale_id, args.reason, args.operator, args.expected_revision, replacement)
        db.commit()
        print(f'訂正記録を追加しました: {correction_id}（元の売上行は変更していません）')


if __name__ == '__main__':
    main()
