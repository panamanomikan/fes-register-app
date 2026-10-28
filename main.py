import os
import uuid
import json
import re
from datetime import date
import hashlib
import logging
from typing import Optional
from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException, status, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field, model_validator
from supabase import create_client, Client
from dotenv import load_dotenv
import database
import reporting
from sales_service import record_sale

load_dotenv()
database.initialize_schema()

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
    request_id: uuid.UUID
    total_amount: int = Field(strict=True, gt=0, le=2147483647)
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
    student_id: str = Field(pattern=r'^[A-Za-z0-9]+$', max_length=100)
    shift_slots: int = Field(strict=True, ge=0, le=2147483647)


def validate_student_id(value):
    if not value or not re.fullmatch(r'[A-Za-z0-9]{1,100}', value):
        raise HTTPException(422, '学籍番号は空白なしの半角英数字で入力してください')


def validate_item(name, price, material_fee, sale_price=None, is_sale=False):
    if not name.strip() or price < 0 or (material_fee is not None and material_fee < 0):
        raise HTTPException(status_code=422, detail='商品名を入力し、金額は0以上にしてください')
    if sale_price is not None and sale_price < 0:
        raise HTTPException(status_code=422, detail='セール価格は0以上にしてください')
    if is_sale and sale_price is None:
        raise HTTPException(status_code=422, detail='セール有効時はセール価格を入力してください')
    if any(value is not None and value > 2147483647 for value in (price, material_fee, sale_price)):
        raise HTTPException(status_code=422, detail='金額が大きすぎます')

class PublicItem(BaseModel):
    id: int
    name: str
    price: int
    category: Optional[str] = None
    image_url: Optional[str] = None
    sale_price: Optional[int] = None
    is_sale: Optional[bool] = False


@app.get("/items", response_model=list[PublicItem])
def read_items(response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    # Explicit allowlist: never send author IDs, costs or private remarks to the register.
    fields = tuple(PublicItem.model_fields)
    return [{field: getattr(item, field) for field in fields}
            for item in db.query(database.Item).order_by(database.Item.id).all()]


@app.get('/admin/items')
def read_admin_items(response: Response, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    response.headers['Cache-Control'] = 'private, no-store'
    return db.query(database.Item).order_by(database.Item.id).all()

@app.post("/items")
def create_item(
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form("一般"),
    creator_id: str = Form(...),
    material_fee: int = Form(...),
    remarks: str = Form(""),
    file: UploadFile = File(...),
    request_id: Optional[uuid.UUID] = Form(None),
    db: Session = Depends(get_db)
):
    validate_item(name, price, material_fee)
    validate_student_id(creator_id)
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
    validate_student_id(creator_id)
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
def create_sale(sale: SaleCreate, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return record_sale(db, sale)

@app.get("/sales")
def read_sales(start: Optional[date] = None, end: Optional[date] = None,
               db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return reporting.selected_sales(db, start, end)

@app.get("/reports/summary")
def report_summary(start: Optional[date] = None, end: Optional[date] = None,
                   db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    sales = reporting.selected_sales(db, start, end)
    result = reporting.calculate(sales, db.query(database.Member).all())
    result.pop('products')
    result['sales'] = sales
    return result

@app.get("/export/overall")
def export_overall_csv(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return reporting.export(db, 'overall')

@app.get("/export/distribution")
def export_distribution_csv(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return reporting.export(db, 'distribution')

@app.get('/export/period')
def export_period_csv(start: date, end: date, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return reporting.export(db, 'period', start, end)

@app.get('/reports/dashboard')
def dashboard_report(start: date, end: date, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return reporting.dashboard(db, start, end)

app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/")
def read_root(admin: str = Depends(get_admin_user)): return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})
@app.get("/admin")
def read_dashboard(admin: str = Depends(get_admin_user)): return FileResponse("static/dashboard.html", headers={"Cache-Control": "no-store"})
