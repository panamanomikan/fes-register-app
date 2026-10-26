import os
import uuid
import json
import csv
import math
import hashlib
import logging
from io import StringIO
from typing import Optional, List
from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, model_validator
from supabase import create_client, Client
from dotenv import load_dotenv
import database

load_dotenv()
database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()
security = HTTPBasic(auto_error=False)

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("【致命的エラー】環境変数が不足しています")

supabase: Client = create_client(supabase_url, supabase_key)

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"

def get_admin_user(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials is None or credentials.username != ADMIN_USERNAME or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def get_db():
    db = database.SessionLocal()
    try: yield db
    finally: db.close()

# Pydanticモデル
class SaleCreate(BaseModel):
    total_amount: int = Field(gt=0, le=2147483647)
    items_json: str

    @model_validator(mode='after')
    def validate_details(self):
        try:
            items = json.loads(self.items_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError('購入内訳が不正です') from exc
        if not isinstance(items, dict) or not items:
            raise ValueError('購入内訳が空です')
        total = 0
        for info in items.values():
            if not isinstance(info, dict):
                raise ValueError('購入内訳の形式が不正です')
            price, quantity = info.get('price'), info.get('quantity')
            if type(price) is not int or price < 0 or type(quantity) is not int or quantity <= 0:
                raise ValueError('単価・数量が不正です')
            total += price * quantity
        if total != self.total_amount:
            raise ValueError('合計と購入内訳が一致しません')
        return self

class MemberUpdate(BaseModel):
    student_id: str
    shift_slots: int = Field(ge=0, le=2147483647)


def validate_item(name, price, material_fee, sale_price=None, is_sale=False):
    if not name.strip() or price < 0 or (material_fee is not None and material_fee < 0):
        raise HTTPException(status_code=422, detail='商品名を入力し、金額は0以上にしてください')
    if sale_price is not None and sale_price < 0:
        raise HTTPException(status_code=422, detail='セール価格は0以上にしてください')
    if is_sale and sale_price is None:
        raise HTTPException(status_code=422, detail='セール有効時はセール価格を入力してください')

@app.get("/items")
def read_items(db: Session = Depends(get_db)):
    return db.query(database.Item).order_by(database.Item.id).all()

@app.post("/items")
def create_item(
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form("一般"),
    creator_id: str = Form(None),
    material_fee: int = Form(0),
    remarks: str = Form(""),
    file: UploadFile = File(...),
    request_id: Optional[uuid.UUID] = Form(None),
    db: Session = Depends(get_db)
):
    validate_item(name, price, material_fee)
    # 同期I/OはFastAPIのワーカースレッドで処理する。
    content = file.file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='画像は20MB以下にしてください')
    if not content:
        raise HTTPException(status_code=422, detail='画像が空です')
    request_key = str(request_id or uuid.uuid4())  # 旧画面との互換性
    payload = json.dumps([name, price, category, creator_id, material_fee, remarks, file.content_type], ensure_ascii=False)
    payload_hash = hashlib.sha256(payload.encode() + b'\0' + content).hexdigest()

    def saved_result(receipt):
        if receipt.payload_hash != payload_hash:
            raise HTTPException(status_code=409, detail='受付番号と登録内容が一致しません。元の内容で再送してください。')
        return {'message': 'Success', 'item_id': receipt.item_id, 'request_id': request_key}

    try:
        existing = db.get(database.ItemRegistration, request_key)
        if existing:
            return saved_result(existing)
        receipt = database.ItemRegistration(request_id=request_key, payload_hash=payload_hash)
        db.add(receipt)
        try:
            # DBの一意制約で、別ワーカーからの同じ受付番号も直列化する。
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.get(database.ItemRegistration, request_key)
            if existing:
                return saved_result(existing)
            raise
        file_ext = file.filename.split(".")[-1]
        file_name = f"{uuid.uuid4()}.{file_ext}"
        supabase.storage.from_("item-images").upload(
            path=file_name, file=content, file_options={"content-type": file.content_type}
        )
        image_url = supabase.storage.from_("item-images").get_public_url(file_name)
        
        db_item = database.Item(
            name=name, price=price, category=category, 
            image_url=image_url, creator_id=creator_id,
            material_fee=material_fee, remarks=remarks
        )
        db.add(db_item)
        db.flush()
        receipt.item_id = db_item.id
        result = {'message': 'Success', 'item_id': db_item.id, 'request_id': request_key}
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logging.exception('Item registration failed: request_id=%s', request_key)
        raise HTTPException(status_code=503, detail='保存結果を確認できません。同じ受付番号で再確認してください。')


@app.get('/item-registrations/{request_id}')
def read_item_registration(request_id: uuid.UUID, db: Session = Depends(get_db)):
    receipt = db.get(database.ItemRegistration, str(request_id))
    if not receipt:
        raise HTTPException(status_code=404, detail='登録完了はまだ確認できません')
    return {'status': 'completed', 'item_id': receipt.item_id}

@app.put("/items/{item_id}")
def update_item_full(
    item_id: int,
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form("一般"),
    sale_price: Optional[int] = Form(None),
    is_sale: bool = Form(False),
    creator_id: Optional[str] = Form(None),
    material_fee: Optional[int] = Form(0),
    remarks: Optional[str] = Form(""),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    admin: str = Depends(get_admin_user)
):
    validate_item(name, price, material_fee, sale_price, is_sale)
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: raise HTTPException(status_code=404, detail="商品が見つかりません")

    db_item.name = name
    db_item.price = price
    db_item.category = category
    db_item.sale_price = sale_price
    db_item.is_sale = is_sale
    db_item.creator_id = creator_id
    db_item.material_fee = material_fee
    db_item.remarks = remarks

    if file and file.filename:
        file_ext = file.filename.split(".")[-1]
        file_name = f"{uuid.uuid4()}.{file_ext}"
        file_content = file.file.read()
        supabase.storage.from_("item-images").upload(
            path=file_name, file=file_content, file_options={"content-type": file.content_type}
        )
        db_item.image_url = supabase.storage.from_("item-images").get_public_url(file_name)

    db.commit()
    return {"message": "Updated"}

@app.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: raise HTTPException(status_code=404, detail="商品が見つかりません")
    db.delete(db_item)
    db.commit()
    return {"message": "Deleted"}

# --- メンバー管理API ---
@app.get("/members")
def get_members(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return db.query(database.Member).all()

@app.post("/members")
def upsert_member(m: MemberUpdate, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_member = db.query(database.Member).filter(database.Member.student_id == m.student_id).first()
    if db_member:
        db_member.shift_slots = m.shift_slots
    else:
        db_member = database.Member(student_id=m.student_id, shift_slots=m.shift_slots)
        db.add(db_member)
    db.commit()
    return {"message": "Success"}

@app.delete("/members/{student_id}")
def delete_member(student_id: str, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_member = db.query(database.Member).filter(database.Member.student_id == student_id).first()
    if db_member:
        db.delete(db_member)
        db.commit()
    return {"message": "Deleted"}

# --- 売上・CSV関連 ---
@app.post("/sales")
def create_sale(sale: SaleCreate, db: Session = Depends(get_db)):
    db_sale = database.Sale(total_amount=sale.total_amount, items_json=sale.items_json)
    db.add(db_sale)
    db.commit()
    return {"message": "Recorded"}

@app.get("/sales")
def read_sales(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return db.query(database.Sale).order_by(database.Sale.id).all()

# CSV 1: 全体集計 (改修版)
@app.get("/export/overall")
def export_overall_csv(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    sales = db.query(database.Sale).all()
    summary = {}
    total_revenue = 0
    total_group_return = 0

    for s in sales:
        if not s.items_json: continue
        items = json.loads(s.items_json)
        for name, info in items.items():
            name = info.get('name', name) if isinstance(info, dict) else name
            if isinstance(info, int):
                info = {'quantity': info, 'price': 0}
            price = info.get('price', 0)
            qty = info.get('quantity', 0)
            creator_id = info.get('creator_id', 'unknown')
            cost = info.get('material_fee', 0)
            remarks = info.get('remarks', '')
            is_sale = info.get('is_sale', False)
            
            # 作品名の頭に「【セール】」を付与して区別する
            display_name = f"【セール】{name}" if is_sale else name
            
            key = (creator_id, display_name, cost, price, remarks)
            summary[key] = summary.get(key, 0) + qty
            total_revenue += price * qty
            total_group_return += math.floor(price * qty * 0.1)

    output = StringIO()
    output.write('\ufeff') # BOMの付与
    writer = csv.writer(output)
    writer.writerow(["製作者学籍番号", "作品名", "材料費", "販売価格", "作者利益", "シフト代", "団体還元", "個数", "備考", "売り上げ", "総売り上げ", "団体還元送金額"])
    
    first_row = True
    for key, qty in summary.items():
        creator_id, name, cost, price, remarks = key
        row = [
            creator_id, name, f"¥{cost}", f"¥{price}",
            f"¥{math.floor(price * 0.7)}", f"¥{math.floor(price * 0.2)}", f"¥{math.floor(price * 0.1)}",
            qty, remarks, f"¥{price * qty}"
        ]
        if first_row:
            row.extend([f"¥{total_revenue}", f"¥{total_group_return}"])
            first_row = False
        writer.writerow(row)

    res = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    res.headers["Content-Disposition"] = "attachment; filename=overall_report.csv"
    return res

# CSV 2: 個人分配集計
@app.get("/export/distribution")
def export_distribution_csv(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    members = db.query(database.Member).all()
    sales = db.query(database.Sale).all()
    
    total_shift_pool = 0
    total_slots = sum([m.shift_slots for m in members])
    author_profits = {}

    for s in sales:
        if not s.items_json: continue
        items = json.loads(s.items_json)
        for name, info in items.items():
            if isinstance(info, int):
                info = {'quantity': info, 'price': 0}
            price = info.get('price', 0)
            qty = info.get('quantity', 0)
            creator_id = info.get('creator_id', 'unknown')
            
            total_shift_pool += (price * qty * 0.2)
            profit = math.floor(price * qty * 0.7)
            author_profits[creator_id] = author_profits.get(creator_id, 0) + profit

    pay_per_slot = (total_shift_pool / total_slots) if total_slots > 0 else 0

    output = StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["学籍番号", "シフトコマ数", "シフト代金", "作者利益", "総収入"])

    processed_ids = set()
    for m in members:
        shift_pay = math.floor(pay_per_slot * m.shift_slots)
        author_profit = author_profits.get(m.student_id, 0)
        writer.writerow([m.student_id, m.shift_slots, shift_pay, author_profit, shift_pay + author_profit])
        processed_ids.add(m.student_id)

    for cid, profit in author_profits.items():
        if cid not in processed_ids and cid != 'unknown':
            writer.writerow([cid, 0, 0, profit, profit])

    res = StreamingResponse(iter([output.getvalue()]), media_type="text/csv")
    res.headers["Content-Disposition"] = "attachment; filename=distribution_report.csv"
    return res

app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/")
def read_root(): return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})
@app.get("/admin")
def read_dashboard(admin: str = Depends(get_admin_user)): return FileResponse("static/dashboard.html")
