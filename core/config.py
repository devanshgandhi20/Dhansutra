import os
import warnings

from dotenv import load_dotenv

load_dotenv()

# Values that ship in the repo. They must never be used to sign real tokens.
_INSECURE_DEFAULTS = {
    "supersecretkey_change_me_in_production",
    "superrefreshkey_change_me_in_production",
}
INSECURE_SYNC_TOKEN = "choose_a_strong_random_secret_token_here"


def _optional_int(name: str):
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else None


class Settings:
    # Environment mode
    ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

    # Database: Render PostgreSQL provides 'postgres://', but SQLAlchemy requires 'postgresql://'
    _raw_db = os.getenv("DB_URL", os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dhansutra"))
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

    # Gmail / Apps Script webhook. The webhook has no user login, so it must be
    # tied to one DhanSutra user (DHANSUTRA_SYNC_USER_ID) and a secret token.
    SYNC_TOKEN = os.getenv("DHANSUTRA_SYNC_TOKEN", INSECURE_SYNC_TOKEN)
    SYNC_USER_ID = _optional_int("DHANSUTRA_SYNC_USER_ID")


settings = Settings()


def _check_secrets(s: Settings) -> None:
    problems = []
    if s.SECRET_KEY in _INSECURE_DEFAULTS:
        problems.append("SECRET_KEY is still the built-in default")
    if s.SECRET_REFRESH_KEY in _INSECURE_DEFAULTS:
        problems.append("SECRET_REFRESH_KEY is still the built-in default")
    if s.SECRET_KEY == s.SECRET_REFRESH_KEY:
        problems.append("SECRET_KEY and SECRET_REFRESH_KEY must be different values")
    if not problems:
        return
    message = "Insecure auth configuration: " + "; ".join(problems) + ". Set strong random values in the environment."
    if s.ENVIRONMENT == "production":
        # Anyone who knows the default key can forge a login token for any user.
        raise RuntimeError(message)
    warnings.warn(message)


_check_secrets(settings)
