"""Session helpers shared by the login, Google login, refresh and logout routes.

A session = one access cookie + one refresh cookie. Every refresh token has its
own Redis key (refresh:<user_id>:<jti>), so logging in on a second device no
longer silently invalidates the first one, and logout only ends *this* device.
"""
from typing import Optional

from fastapi import Response

from core.config import settings
from modules.auth.jwtutils import (
    create_access_token,
    create_refresh_token_with_jti,
    verify_refresh_token,
)
from modules.redis.redis_client import redis_client

IS_PRODUCTION = settings.ENVIRONMENT == "production"
ACCESS_MAX_AGE = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
REFRESH_MAX_AGE = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400


def _refresh_key(user_id, jti: str) -> str:
    return f"refresh:{user_id}:{jti}"


def set_access_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        key="access_token", value=access_token, max_age=ACCESS_MAX_AGE,
        httponly=True, samesite="lax", secure=IS_PRODUCTION, path="/",
    )


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key="refresh_token", value=refresh_token, max_age=REFRESH_MAX_AGE,
        httponly=True, samesite="lax", secure=IS_PRODUCTION, path="/",
    )


def start_session(response: Response, user_id: int) -> None:
    """Issue both tokens, remember the refresh token in Redis and set the cookies.
    Raises redis.exceptions.RedisError if Redis is unreachable."""
    access_token = create_access_token(user_id)
    refresh_token, jti = create_refresh_token_with_jti(user_id)
    redis_client.set(_refresh_key(user_id, jti), "1", ex=REFRESH_MAX_AGE)
    set_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)


def refresh_user_id(refresh_token: Optional[str]) -> Optional[int]:
    """User id if this refresh token is genuine, unexpired and not revoked, else None.
    Raises redis.exceptions.RedisError if Redis is unreachable."""
    if not refresh_token:
        return None
    payload = verify_refresh_token(refresh_token)
    if not payload:
        return None
    sub, jti = payload.get("sub"), payload.get("jti")
    if not sub or not jti:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    if not redis_client.exists(_refresh_key(user_id, jti)):
        return None
    return user_id


def end_session(refresh_token: Optional[str]) -> None:
    """Revoke this device's refresh token. Raises RedisError if Redis is unreachable."""
    if not refresh_token:
        return
    payload = verify_refresh_token(refresh_token)
    if payload and payload.get("sub") and payload.get("jti"):
        redis_client.delete(_refresh_key(payload["sub"], payload["jti"]))


def end_all_sessions(user_id: int) -> None:
    """Revoke every refresh token of a user (all devices)."""
    for key in redis_client.scan_iter(match=f"refresh:{user_id}:*"):
        redis_client.delete(key)
