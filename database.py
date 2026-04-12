import os
from sqlalchemy import create_engine, Column, Integer, DateTime, String
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

# --- 売上テーブル ---
class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    total_amount = Column(Integer, nullable=False)
    items_json = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# --- 商品マスタテーブル（categoryを追加しました！） ---
class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    category = Column(String, default="一般") # ← これがエラーの原因でした
    image_url = Column(String, nullable=True)