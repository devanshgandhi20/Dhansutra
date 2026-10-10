import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Environment mode
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

    # Database: Render PostgreSQL provides 'postgres://', but SQLAlchemy requires 'postgresql://'
    _raw_db = os.getenv("DB_URL", os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/practice_db"))
    if _raw_db and _raw_db.startswith("postgres://"):
        _raw_db = _raw_db.replace("postgres://", "postgresql://", 1)
    DB_URL = _raw_db

    # Redis Cache / Rate Limiting (Render Redis or Upstash)
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    # If using rediss:// SSL URLs from managed providers:
    REDIS_URL = os.getenv("REDIS_URL", None)

    # Auth & Security (Must be injected via environment in production)
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey_change_me_in_production")
    SECRET_REFRESH_KEY = os.getenv("SECRET_REFRESH_KEY", "superrefreshkey_change_me_in_production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")

    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
    REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))

    # Google OAuth (Do NOT hardcode these here)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

settings = Settings()