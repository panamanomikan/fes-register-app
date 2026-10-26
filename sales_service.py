"""Atomic sale submission and developer-only, append-only corrections."""
import hashlib
import json
import logging
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
import database


def record_sale(db, sale):
    key = str(sale.request_id)
    details = json.loads(sale.items_json)
    digest = hashlib.sha256(json.dumps([sale.total_amount, details], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def saved(receipt):
        if receipt.payload_hash != digest:
            raise HTTPException(409, '同じ受付番号の内容を変更できません')
        return dict(message='Recorded', sale_id=receipt.sale_id, request_id=key)

    try:
        receipt = db.get(database.SaleSubmission, key)
        if receipt:
            return saved(receipt)
        receipt = database.SaleSubmission(request_id=key, payload_hash=digest)
        db.add(receipt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.get(database.SaleSubmission, key)
            if existing:
                return saved(existing)
            raise
        ids = [i.get('item_id') for i in details.values()]
        if any(type(i) is not int or i <= 0 for i in ids):
            raise HTTPException(422, '登録商品を選んでください。手動入力は廃止しました。')
        catalog = {i.id: i for i in db.query(database.Item).filter(database.Item.id.in_(ids)).order_by(database.Item.id).with_for_update().all()}
        canonical = {}
        for key_in_cart, info in details.items():
            item = catalog.get(info['item_id'])
            price = (item.sale_price if item.is_sale and item.sale_price is not None else item.price) if item else None
            if not item or price != info['price']:
                raise HTTPException(409, dict(code='catalog_changed', message='価格の変更または商品の削除がありました。カートを取消して、最新の商品を選び直してください。'))
            canonical[key_in_cart] = dict(item_id=item.id, name=item.name, price=price,
                quantity=info['quantity'], is_sale=bool(item.is_sale), original_price=item.price,
                creator_id=item.creator_id, material_fee=item.material_fee, remarks=item.remarks)
        record = database.Sale(total_amount=sale.total_amount, items_json=json.dumps(canonical, ensure_ascii=False))
        db.add(record)
        db.flush()
        receipt.sale_id = record.id
        result = saved(receipt)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logging.exception('Sale submission failed: request_id=%s', key)
        raise HTTPException(503, '同じ受付番号で再確認してください')


def correct_sale(db, sale_id, reason, operator, expected_revision, replacement=None):
    """No HTTP route. Caller must explicitly commit. None replacement means void."""
    if not reason.strip() or not operator.strip():
        raise ValueError('訂正理由と作業者は必須です')
    original = db.query(database.Sale).filter_by(id=sale_id).with_for_update().first()
    if not original:
        raise ValueError('取引がありません')
    last = db.query(database.SaleCorrection).filter_by(sale_id=sale_id).order_by(database.SaleCorrection.id.desc()).first()
    if (last.id if last else 0) != expected_revision:
        raise ValueError('別の訂正が入りました。最新履歴を確認してください')
    total, items = (0, '{}') if replacement is None else (replacement.total_amount, replacement.items_json)
    correction = database.SaleCorrection(sale_id=sale_id,
        before_total=last.after_total if last else original.total_amount,
        before_items=last.after_items if last else original.items_json or '{}',
        after_total=total, after_items=items, reason=reason.strip(), corrected_by=operator.strip())
    db.add(correction)
    db.flush()
    return correction.id
