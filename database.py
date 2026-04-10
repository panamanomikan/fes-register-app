import os
from sqlalchemy import create_engine, Column, Integer, DateTime, String
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
from dotenv import load_dotenv # ← 【追加】dotenvをインポート

# 【追加】ローカルにある .env ファイルを読み込んで環境変数にセットする
load_dotenv() 

# 1. 接続先のURLを設定
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("【致命的エラー】DATABASE_URL環境変数が設定されていません！.envファイルかRenderの設定を確認してください。")

# SQLAlchemyの仕様対応
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 2. データベースとの通信役（エンジン）を作成
engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 3. テーブルの設計図（売上データを記録する表）
class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    total_amount = Column(Integer, nullable=False)
    items_json = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))