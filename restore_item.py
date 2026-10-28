"""Developer-only restoration. Preview unless --apply is supplied."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('item_id', type=int)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    import database
    with database.SessionLocal() as db:
        item = db.query(database.Item).filter_by(id=args.item_id).with_for_update().first()
        if item is None:
            raise ValueError('商品がありません。以前に完全削除された商品は復元できません。')
        print(json.dumps(dict(id=item.id, name=item.name, deleted_at=str(item.deleted_at)), ensure_ascii=False))
        if not args.apply:
            print('プレビューのみ。復元する場合は --apply を指定してください。')
            return
        item.deleted_at = None
        db.commit()
        print('同じ商品IDで復元しました。レジの商品一覧を更新してください。')


if __name__ == '__main__':
    main()
