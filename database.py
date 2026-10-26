import os
from sqlalchemy import create_engine, Column, Integer, DateTime, String, Boolean, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv() 

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("【致命的エラー】DATABASE_URL環境変数が設定されていません！")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
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


class ItemRegistration(Base):
    __tablename__ = "item_registrations"

    request_id = Column(String(36), primary_key=True)
    payload_hash = Column(String(64), nullable=False)
    item_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class SaleSubmission(Base):
    __tablename__ = 'sale_submissions'
    request_id = Column(String(36), primary_key=True)
    payload_hash = Column(String(64), nullable=False)
    sale_id = Column(Integer, ForeignKey('sales.id'), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class SaleCorrection(Base):
    __tablename__ = 'sale_corrections'
    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey('sales.id'), nullable=False, index=True)
    before_total = Column(Integer, nullable=False)
    before_items = Column(String, nullable=False)
    after_total = Column(Integer, nullable=False)
    after_items = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    corrected_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


def initialize_schema():
    # New tables and their access restrictions become visible atomically.
    with engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            # Serialize startup DDL when multiple application workers boot together.
            connection.execute(text('SELECT pg_advisory_xact_lock(746281930)'))
        Base.metadata.create_all(bind=connection)
        if connection.dialect.name == 'postgresql':
            for table in ('sale_submissions', 'sale_corrections'):
                connection.execute(text(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY'))
                connection.execute(text(f'REVOKE ALL ON public.{table} FROM PUBLIC, anon, authenticated'))
            connection.execute(text('''CREATE OR REPLACE FUNCTION public.reject_sale_correction_change()
                RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
                BEGIN RAISE EXCEPTION 'Sale corrections are append-only'; END; $$'''))
            connection.execute(text('REVOKE ALL ON FUNCTION public.reject_sale_correction_change() FROM PUBLIC'))
            connection.execute(text('''DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'sale_corrections_append_only'
                    AND tgrelid = 'public.sale_corrections'::regclass) THEN
                    CREATE TRIGGER sale_corrections_append_only BEFORE UPDATE OR DELETE
                    ON public.sale_corrections FOR EACH ROW EXECUTE FUNCTION public.reject_sale_correction_change();
                END IF;
            END; $$'''))
