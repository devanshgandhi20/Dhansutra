from fastapi import APIRouter, Cookie
from fastapi.responses import RedirectResponse
from modules.redis.redis_client import redis_client
from modules.auth.jwtutils import verify_refresh_token

router = APIRouter(prefix="/auth")

@router.get("/logout")
def logout(
    access_token: str = Cookie(None),
    refresh_token: str = Cookie(None)
):
    if refresh_token:
        payload = verify_refresh_token(refresh_token)
        if payload and "sub" in payload:
            redis_client.delete(f"refresh:{payload['sub']}")
            
    res = RedirectResponse(url="/login")
    res.delete_cookie("access_token")
    res.delete_cookie("refresh_token")
    return res