from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker,declarative_base
import os

DB_URL = os.getenv(
    "DB_URL",
    "postgresql://postgres:postgres@postgres:5432/dhansutra_db"
)

from core.config import settings

# Add pool_pre_ping and pool_recycle
engine = create_engine(
    settings.DB_URL,
    pool_pre_ping=True,      # Tests connection liveness before executing queries
    pool_recycle=300,        # Automatically recycles connections older than 5 minutes
    pool_size=5,             # Sensible pool size for free tier
    max_overflow=10
)
SessionLocal = sessionmaker(autocommit = False, autoflush=False, bind=engine)

Base = declarative_base()