import os
from sqlalchemy import create_engine, Column, Integer, DateTime, String, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv() 

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("【致命的エラー】DATABASE_URL環境変数が設定されていません！")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    total_amount = Column(Integer, nullable=False)
    items_json = Column(String, nullable=True) # 会計時の商品情報（価格、製作者ID、原価等を保持）
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    category = Column(String, default="一般")
    image_url = Column(String, nullable=True)
    sale_price = Column(Integer, nullable=True)
    is_sale = Column(Boolean, default=False)
    
    # --- 新規追加項目 ---
    creator_id = Column(String, nullable=True)   # 製作者の学籍番号
    material_fee = Column(Integer, default=0)    # 材料費
    remarks = Column(String, nullable=True)      # 備考

class Member(Base):
    __tablename__ = "members"
    
    student_id = Column(String, primary_key=True) # 学籍番号を主キーに
    shift_slots = Column(Integer, default=0)      # シフトのコマ数