from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker,declarative_base
import os

DB_URL = os.getenv(
    "DB_URL",
    "postgresql://postgres:postgres@postgres:5432/dhansutra_db"
)

engine = create_engine(DB_URL)

SessionLocal = sessionmaker(autocommit = False, autoflush=False, bind=engine)

Base = declarative_base()