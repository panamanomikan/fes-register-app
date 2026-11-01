"""Read-only popularity based on effective sale snapshots; never modifies sales."""
import json
from collections import Counter
from datetime import datetime, timedelta

import database


def recent_item_ids(db, now=None):
    now = now or datetime.utcnow()
    sales = db.query(database.Sale).filter(
        database.Sale.created_at >= now - timedelta(minutes=60),
        database.Sale.created_at <= now,
    ).all()
    latest = {}
    if sales:
        for correction in db.query(database.SaleCorrection).filter(
            database.SaleCorrection.sale_id.in_([sale.id for sale in sales])
        ).order_by(database.SaleCorrection.id):
            latest[correction.sale_id] = correction
    counts = Counter()
    for sale in sales:
        correction = latest.get(sale.id)
        if correction and correction.after_total == 0:
            continue
        details = json.loads(correction.after_items if correction else sale.items_json)
        for info in details.values():
            item_id, quantity = info.get('item_id'), info.get('quantity')
            if type(item_id) is int and type(quantity) is int and quantity > 0:
                counts[item_id] += quantity
    active_ids = {row[0] for row in db.query(database.Item.id).filter(
        database.Item.deleted_at.is_(None)
    ).all()}
    return sorted((item_id for item_id in counts if item_id in active_ids),
                  key=lambda item_id: (-counts[item_id], item_id))[:10]
