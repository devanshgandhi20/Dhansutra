import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from jose import jwt, JWTError
from core.config import settings

SECRET_KEY = settings.SECRET_KEY
SECRET_REFRESH_KEY = settings.SECRET_REFRESH_KEY
ALGORITHM = settings.ALGORITHM


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int) -> str:
    now = _now()
    to_encode = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token_with_jti(user_id: int) -> Tuple[str, str]:
    """Refresh token plus its unique id (jti). The jti is what gets stored in Redis,
    so each login/device has its own revocable session."""
    now = _now()
    jti = uuid.uuid4().hex
    to_encode = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(to_encode, SECRET_REFRESH_KEY, algorithm=ALGORITHM), jti


def create_refresh_token(user_id: int) -> str:
    return create_refresh_token_with_jti(user_id)[0]


def _decode(token: str, key: str, expected_type: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    # A token of the wrong kind must never be accepted in place of the right one.
    if payload.get("type") != expected_type:
        return None
    return payload


def verify_access_token(token: str) -> Optional[dict]:
    return _decode(token, SECRET_KEY, "access")


def verify_refresh_token(token: str) -> Optional[dict]:
    return _decode(token, SECRET_REFRESH_KEY, "refresh")
