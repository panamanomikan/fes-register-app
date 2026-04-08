from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles # ← 追加
from fastapi.responses import FileResponse  # ← 追加
from pydantic import BaseModel
from sqlalchemy.orm import Session
import database

database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

class SaleCreate(BaseModel):
    total_amount: int

@app.post("/sales")
def create_sale(sale: SaleCreate, db: Session = Depends(get_db)):
    db_sale = database.Sale(total_amount=sale.total_amount)
    db.add(db_sale)
    db.commit()
    db.refresh(db_sale)
    return {"message": "売上を記録しました", "sale_data": db_sale}

@app.get("/sales")
def read_sales(db: Session = Depends(get_db)):
    sales = db.query(database.Sale).all()
    return sales

# --- ここから下を変更 ---

# staticフォルダの中身を配信できるようにする設定
app.mount("/static", StaticFiles(directory="static"), name="static")

# http://127.0.0.1:8000 にアクセスしたとき、index.htmlを返す
@app.get("/")
def read_root():
    return FileResponse("static/index.html")