import os
import uuid
from typing import Optional
from fastapi import FastAPI, Depends, Form, UploadFile, File, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
import database

load_dotenv()
database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()
security = HTTPBasic()

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# 幹部用のパスワード設定
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123" # 当日に変更してください

def get_admin_user(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != ADMIN_USERNAME or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return credentials.username

def get_db():
    db = database.SessionLocal()
    try: yield db
    finally: db.close()

class SaleCreate(BaseModel):
    total_amount: int
    items_json: Optional[str] = None

class ItemUpdate(BaseModel):
    price: int

# --- 一般向けAPI ---
@app.get("/items")
def read_items(db: Session = Depends(get_db)):
    return db.query(database.Item).order_by(database.Item.id).all()

@app.post("/items")
async def create_item(
    name: str = Form(...),
    price: int = Form(...),
    category: str = Form("一般"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        file_ext = file.filename.split(".")[-1]
        file_name = f"{uuid.uuid4()}.{file_ext}"
        file_content = await file.read()
        # Storageに保存
        supabase.storage.from_("item-images").upload(
            path=file_name,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
        image_url = supabase.storage.from_("item-images").get_public_url(file_name)
        
        db_item = database.Item(name=name, price=price, category=category, image_url=image_url)
        db.add(db_item)
        db.commit()
        return {"message": "Success"}
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sales")
def create_sale(sale: SaleCreate, db: Session = Depends(get_db)):
    db_sale = database.Sale(total_amount=sale.total_amount, items_json=sale.items_json)
    db.add(db_sale)
    db.commit()
    return {"message": "Recorded"}

# --- 幹部向けAPI ---
@app.get("/sales")
def read_sales(db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    return db.query(database.Sale).all()

@app.put("/items/{item_id}")
def update_item_price(item_id: int, item_update: ItemUpdate, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: return {"error": "NotFound"}
    db_item.price = item_update.price
    db.commit()
    return {"message": "Updated"}

@app.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), admin: str = Depends(get_admin_user)):
    db_item = db.query(database.Item).filter(database.Item.id == item_id).first()
    if not db_item: return {"error": "NotFound"}
    db.delete(db_item)
    db.commit()
    return {"message": "Deleted"}

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root(): return FileResponse("static/index.html")

@app.get("/admin")
def read_dashboard(admin: str = Depends(get_admin_user)):
    return FileResponse("static/dashboard.html")