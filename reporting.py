"""Reporting uses immutable sale snapshots, JST dates and integer yen only."""
import csv
import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from io import StringIO

import database
from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def selected_sales(db, start=None, end=None):
    if start and end and start > end:
        raise HTTPException(422, '開始日は終了日以前にしてください')
    query = db.query(database.Sale)
    if start:
        query = query.filter(database.Sale.created_at >= datetime.combine(start, time()) - timedelta(hours=9))
    if end:
        query = query.filter(database.Sale.created_at < datetime.combine(end, time()) + timedelta(days=1, hours=-9))
    sales = query.order_by(database.Sale.id).all()
    corrections = defaultdict(list)
    if sales:
        for c in db.query(database.SaleCorrection).filter(database.SaleCorrection.sale_id.in_([s.id for s in sales])).order_by(database.SaleCorrection.id):
            corrections[c.sale_id].append(c)
    result = []
    for s in sales:
        history = corrections[s.id]
        last = history[-1] if history else None
        result.append(dict(id=s.id, created_at=s.created_at,
            total_amount=last.after_total if last else s.total_amount,
            items_json=last.after_items if last else s.items_json,
            original_total=s.total_amount, original_items=s.items_json,
            voided=bool(last and last.after_total == 0),
            corrections=[dict(id=c.id, reason=c.reason, corrected_by=c.corrected_by,
                created_at=c.created_at, before_total=c.before_total, before_items=c.before_items,
                after_total=c.after_total, after_items=c.after_items) for c in history]))
    return result


def calculate(sales, members):
    revenue = 0
    authors = defaultdict(int)
    products = defaultdict(int)
    for s in sales:
        if s['voided']:
            continue
        revenue += s['total_amount']
        for key, info in json.loads(s['items_json'] or '{}').items():
            if not isinstance(info, dict):
                continue  # Legacy entries without prices remain in the unallocated balance.
            price, qty = info.get('price', 0), info.get('quantity', 0)
            cid = info.get('creator_id') or 'unknown'
            authors[cid] += price * qty
            products[(cid, info.get('name', key), price, info.get('material_fee', 0), info.get('remarks', ''), bool(info.get('is_sale')))] += qty
    slots = {m.student_id: m.shift_slots for m in members}
    total_slots = sum(slots.values())
    people = []
    for cid in sorted(set(authors) | set(slots)):
        author = authors[cid] * 70 // 100
        shift = revenue * 20 * slots.get(cid, 0) // (100 * total_slots) if total_slots else 0
        # Unknown legacy recipients are explicitly withheld, never silently paid.
        payable = cid not in ('unknown', 'manual')
        people.append(dict(student_id=cid, shift_slots=slots.get(cid, 0),
            author=author if payable else 0, shift=shift if payable else 0,
            total=author + shift if payable else 0))
    group = revenue * 10 // 100
    return dict(revenue=revenue, count=sum(not s['voided'] for s in sales),
        group=group, people=people, unallocated=revenue-group-sum(p['total'] for p in people),
        products=products, total_slots=total_slots)


def summary_rows(report, start, end):
    return [[], ['対象期間 (JST)', period_label(start, end)],
        ['総売上', report['revenue']], ['団体還元（期間合計の10%・円未満切捨て）', report['group']],
        ['個人支払合計', sum(p['total'] for p in report['people'])],
        ['未配分残額（端数・配分先未設定分を含む）', report['unallocated']],
        ['計算方針', '個人ごと・期間合計で作者70%とシフト20%をそれぞれ円未満切捨て。材料費は差し引かない。'],
        ['注意', '日別の支払額を合算した値と、全期間で一括計算した支払額は端数により異なる。精算は同じ期間で統一。']]


def period_label(start=None, end=None):
    if not start and not end:
        return '全期間'
    if start == end:
        return str(start)
    return f'{start or "開始指定なし"} ～ {end or "終了指定なし"}'


def dashboard(db, start, end):
    if start > end:
        raise HTTPException(422, '開始日は終了日以前にしてください')
    sales = selected_sales(db)
    period = [s for s in sales if start <= (s['created_at'] + timedelta(hours=9)).date() <= end]
    def totals(records):
        active = [s for s in records if not s['voided']]
        return dict(revenue=sum(s['total_amount'] for s in active), count=len(active))
    return dict(overall=totals(sales), period={**totals(period), 'sales': period}, label=period_label(start, end))


def csv_response(rows, filename):
    output = StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    for row in rows:
        # User-entered names/remarks must not become spreadsheet formulas.
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for v in row])
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}.csv'})


def export(db, kind, start=None, end=None):
    report = calculate(selected_sales(db, start, end), db.query(database.Member).all())
    if kind in ('overall', 'period'):
        rows = [['製作者学籍番号', '作品名', '販売単価', '材料費（控除なし）', '備考', 'セール', '個数', '売上']]
        rows += [[*key, qty, key[2]*qty] for key, qty in report['products'].items()]
    else:
        rows = [['学籍番号', 'シフトコマ数', 'シフト代金', '作者利益', '総収入']]
        rows += [[p['student_id'], p['shift_slots'], p['shift'], p['author'], p['total']] for p in report['people']]
    summary = ([[], ['対象期間 (JST)', period_label(start, end)], ['総売上', report['revenue']],
                ['会計件数', report['count']]] if kind == 'period' else summary_rows(report, start, end))
    return csv_response(rows + summary, kind + '_report')
