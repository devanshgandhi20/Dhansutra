from fastapi import Cookie, HTTPException, Depends
from sqlalchemy.orm import Session

from core.database import get_db  # noqa: F401  (re-exported: other modules import it from here)
from modules.users.models import User
from modules.auth.jwtutils import verify_access_token


def get_current_user(
    access_token: str = Cookie(None),
    db: Session = Depends(get_db)
) -> User:
    if not access_token:
        raise HTTPException(status_code=401, detail="session_missing")

    payload = verify_access_token(access_token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="session_expired")

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="session_expired")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="user_not_found")

    return user
