import logging

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from modules.auth.sessions import end_session, refresh_user_id, set_access_cookie
from modules.auth.jwtutils import create_access_token
from modules.auth.userverification import get_db
from modules.redis.rate_limiter import is_rate_limited
from modules.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


@router.post("/refresh")
def refresh_session_api(
    request: Request,
    refresh_token: str = Cookie(None),
    db: Session = Depends(get_db),
):
    """JSON refresh used by the dashboard when an API call comes back 401.
    Issues a new access cookie if the refresh token is still valid."""
    ip = request.client.host if request.client else "unknown"
    if is_rate_limited(f"refresh:{ip}", limit=20, window=60):
        return JSONResponse(status_code=429, content={"detail": "too_many_requests"})

    try:
        user_id = refresh_user_id(refresh_token)
    except RedisError:
        logger.exception("Redis unavailable during token refresh")
        return JSONResponse(status_code=503, content={"detail": "service_unavailable"})

    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not user or not user.is_active:
        return JSONResponse(status_code=401, content={"detail": "session_expired"})

    res = JSONResponse(content={"ok": True})
    set_access_cookie(res, create_access_token(user.id))
    return res


@router.get("/logout")
def logout(refresh_token: str = Cookie(None)):
    try:
        end_session(refresh_token)
    except RedisError:
        logger.exception("Redis unavailable during logout; cookies are still cleared")

    res = RedirectResponse(url="/login", status_code=302)
    res.delete_cookie("access_token", path="/")
    res.delete_cookie("refresh_token", path="/")
    return res
