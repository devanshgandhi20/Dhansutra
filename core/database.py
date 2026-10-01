import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config import settings

# Resolves DB_URL from settings or directly from the OS environment
db_url = getattr(settings, "DB_URL", None) or getattr(settings, "DATABASE_URL", None) or os.getenv("DB_URL") or os.getenv("DATABASE_URL")

engine = create_engine(
    db_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()