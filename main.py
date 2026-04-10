from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional # ← 【修正】Optionalを使うために追加
import database

database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 【修正】ルートB対応：フロントエンドから items_json も受け取れるようにする
class SaleCreate(BaseModel):
    total_amount: int
    items_json: Optional[str] = None # 文字列として受け取る（未送信の場合はNone）

@app.post("/sales")
def create_sale(sale: SaleCreate, db: Session = Depends(get_db)):
    # 【修正】DB保存時に items_json もセットする
    db_sale = database.Sale(
        total_amount=sale.total_amount,
        items_json=sale.items_json
    )
    db.add(db_sale)
    db.commit()
    db.refresh(db_sale)
    return {"message": "売上を記録しました", "sale_data": db_sale}

@app.get("/sales")
def read_sales(db: Session = Depends(get_db)):
    sales = db.query(database.Sale).all()
    return sales

# --- ここから下は変更なし ---

# staticフォルダの中身を配信できるようにする設定
app.mount("/static", StaticFiles(directory="static"), name="static")

# http://127.0.0.1:8000 にアクセスしたとき、index.htmlを返す
@app.get("/")
def read_root():
    return FileResponse("static/index.html")

# http://127.0.0.1:8000/admin にアクセスしたとき、dashboard.htmlを返す
@app.get("/admin")
def read_dashboard():
    return FileResponse("static/dashboard.html")