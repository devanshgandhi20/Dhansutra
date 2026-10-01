from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    # Database
    DB_URL = os.getenv(
        "DB_URL",
        os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/practice_db")
    )

    # Redis Cache / Rate Limiting
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

    # Auth & Security
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey_change_me_in_production")
    SECRET_REFRESH_KEY = os.getenv("SECRET_REFRESH_KEY", "superrefreshkey_change_me_in_production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")

    ACCESS_TOKEN_EXPIRE_MINUTES = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30)
    )

    REFRESH_TOKEN_EXPIRE_DAYS = int(
        os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7)
    )

    # OAuth
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

settings = Settings()