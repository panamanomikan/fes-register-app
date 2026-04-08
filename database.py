from sqlalchemy import create_engine, Column, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

# 1. SQLite用のファイル名を指定（これがPCに保存されるファイルになります）
SQLALCHEMY_DATABASE_URL = "sqlite:///./sales.db"

# 2. データベースとの通信役（エンジン）を作成
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 3. テーブルの設計図（売上データを記録する表）
class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True) # 決済の連番
    total_amount = Column(Integer, nullable=False)     # 売上合計金額
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc)) # 決済時刻（絶対の要件！）