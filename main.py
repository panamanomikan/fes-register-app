import os
import uuid
import json
import csv
import math
from io import StringIO
from typing import Optional, List
from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
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
    total_amount: int
    items_json: Optional[str] = None

class MemberUpdate(BaseModel):
    student_id: str
    shift_slots: int

@app.get("/items")
def read_items(db: Session = Depends(get_db)):
    return db.query(database.Item).order_by(database.Item.id).all()

@app.post("/items")
async def create_item(
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form("一般"),
    creator_id: str = Form(None),
    material_fee: int = Form(0),
    remarks: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        file_ext = file.filename.split(".")[-1]
        file_name = f"{uuid.uuid4()}.{file_ext}"
        file_content = await file.read()
        supabase.storage.from_("item-images").upload(
            path=file_name, file=file_content, file_options={"content-type": file.content_type}
        )
        image_url = supabase.storage.from_("item-images").get_public_url(file_name)
        
        db_item = database.Item(
            name=name, price=price, category=category, 
            image_url=image_url, creator_id=creator_id,
            material_fee=material_fee, remarks=remarks
        )
        db.add(db_item)
        db.commit()
        return {"message": "Success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/items/{item_id}")
async def update_item_full(
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
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: return {"error": "NotFound"}

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
        file_content = await file.read()
        supabase.storage.from_("item-images").upload(
            path=file_name, file=file_content, file_options={"content-type": file.content_type}
        )
        db_item.image_url = supabase.storage.from_("item-images").get_public_url(file_name)

    db.commit()
    return {"message": "Updated"}

@app.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: return {"error": "NotFound"}
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
    return db.query(database.Sale).all()

# CSV 1: 全体集計
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
            price = info.get('price', 0)
            qty = info.get('quantity', 0)
            creator_id = info.get('creator_id', 'unknown')
            cost = info.get('material_fee', 0)
            remarks = info.get('remarks', '')
            
            key = (creator_id, name, cost, price, remarks)
            summary[key] = summary.get(key, 0) + qty
            total_revenue += price * qty
            total_group_return += math.floor(price * qty * 0.1)

    output = StringIO()
    output.write('\ufeff') # 【重要】Excelでの文字化けを防ぐBOMを追加
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
            price = info.get('price', 0)
            qty = info.get('quantity', 0)
            creator_id = info.get('creator_id', 'unknown')
            
            total_shift_pool += (price * qty * 0.2)
            profit = math.floor(price * qty * 0.7)
            author_profits[creator_id] = author_profits.get(creator_id, 0) + profit

    pay_per_slot = (total_shift_pool / total_slots) if total_slots > 0 else 0

    output = StringIO()
    output.write('\ufeff') # 【重要】Excelでの文字化けを防ぐBOMを追加
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
def read_root(): return FileResponse("static/index.html")
@app.get("/admin")
def read_dashboard(admin: str = Depends(get_admin_user)): return FileResponse("static/dashboard.html")