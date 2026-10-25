import logging
from urllib.parse import quote

from fastapi import Cookie, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis.exceptions import RedisError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

import modules.finance.models  # noqa: F401  Registers finance models
import modules.users.models  # noqa: F401  Registers User
from core.config import settings
from core.google_auth import oauth
from modules.auth.auth import MAX_PASSWORD_BYTES, hash_password, verify_password
from modules.auth.jwtutils import create_access_token
from modules.auth.router import router as auth_router
from modules.auth.sessions import IS_PRODUCTION, refresh_user_id, set_access_cookie, start_session
from modules.auth.userverification import get_current_user, get_db
from modules.finance.imports import router as imports_router
from modules.finance.router import router as finance_router
from modules.redis.rate_limiter import check_rate_limit
from modules.users.models import User

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")

app = FastAPI(title="DhanSutra API")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    https_only=IS_PRODUCTION,
    same_site="lax",
)


# PH1.2 — CSRF protection for browser-authenticated API writes.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    path = request.url.path

    # The Gmail webhook authenticates using its own secret token,
    # not the user's browser session cookie.
    is_finance_api = (
        path.startswith("/api/finance/")
        and path != "/api/finance/imports/webhook-ingest"
    )

    is_protected = (
        request.method in UNSAFE_METHODS
        and (
            is_finance_api
            or path in {"/auth/refresh", "/auth/logout"}
        )
    )

    if is_protected:
        # Cross-origin HTML forms cannot send this custom header.
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf_validation_failed"},
            )

        # Reject same-site but cross-origin and fully cross-site
        # browser requests, including those from sibling subdomains.
        fetch_site = request.headers.get("sec-fetch-site", "").lower()

        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf_validation_failed"},
            )

    return await call_next(request)


app.include_router(auth_router)
app.include_router(finance_router)
app.include_router(imports_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

MIN_PASSWORD_LENGTH = 8

LOGIN_ERRORS = {
    "1": "Invalid credentials",
    "session_expired": "Session timed out. Please login again.",
    "too_many_requests": "Too many requests. Please try again later.",
    "session_missing": "Please login first.",
    "user_not_found": "This account was not found or has been disabled.",
    "oauth_failed": "Google sign-in failed. Please try again.",
    "invalid_userinfo": "Google did not return an email address.",
    "email_unverified": "Your Google email address is not verified.",
    "oauth_account_exists": "An account with this email already exists. Please log in with your password.",
    "service_unavailable": "The service is temporarily unavailable. Please try again shortly.",
}

LOGIN_SUCCESS = {
    "user_created": "Account created successfully.",
    "password_reset": "Password updated successfully.",
}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/create_user")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    rate_limit_response = check_rate_limit(key=f"signup:{client_ip(request)}", limit=10, window=3600)
    if rate_limit_response:
        return rate_limit_response

    username = username.strip()
    if len(username) < 3 or len(username) > 255:
        return RedirectResponse(url=f"/signup?error={quote('Username must be 3 to 255 characters')}", status_code=303)
    if len(password) < MIN_PASSWORD_LENGTH:
        return RedirectResponse(
            url=f"/signup?error={quote(f'Password must be at least {MIN_PASSWORD_LENGTH} characters')}",
            status_code=303,
        )
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return RedirectResponse(url=f"/signup?error={quote('Password is too long')}", status_code=303)

    existing_user = db.query(User).filter(func.lower(User.username) == username.lower()).first()
    if existing_user:
        return RedirectResponse(
            url=f"/signup?error={quote('User already exists')}",
            status_code=303
        )
    new_user = User(
        username=username,
        password=hash_password(password)
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        # two signups with the same name raced each other
        db.rollback()
        return RedirectResponse(url=f"/signup?error={quote('User already exists')}", status_code=303)
    return RedirectResponse(url="/login?success=user_created", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = None, success: str = None):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": LOGIN_ERRORS.get(error) if error else None,
            "success": LOGIN_SUCCESS.get(success) if success else None,
        }
    )


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, error: str = None):
    return templates.TemplateResponse("signup.html", {"request": request, "error": error})


@app.get("/forgot_password", response_class=HTMLResponse)
def forgot_password_page(request: Request, error: str = None):
    return templates.TemplateResponse("forgot_password.html", {"request": request, "error": error})


@app.post("/forgot_password")
def forgot_password(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    # SECURITY: this used to set a new password for ANY username with no proof of
    # ownership (no email link, no old password), i.e. anyone could take over any
    # password account. It stays switched off until a verified reset flow
    # (emailed one-time link) exists.
    rate_limit_response = check_rate_limit(key=f"forgot:{client_ip(request)}", limit=3, window=300)
    if rate_limit_response:
        return rate_limit_response
    return RedirectResponse(
        url=f"/forgot_password?error={quote('Password reset is not available yet. Please contact support.')}",
        status_code=302,
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    username = username.strip()
    rate_limit_response = check_rate_limit(key=f"login:{username}:{client_ip(request)}", limit=5, window=60)
    if rate_limit_response:
        return rate_limit_response

    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active or not user.password or not verify_password(password, user.password):
        return RedirectResponse(url="/login?error=1", status_code=302)

    res = RedirectResponse(url="/dashboard", status_code=302)
    try:
        start_session(res, user.id)
    except RedisError:
        logger.exception("Redis unavailable during login")
        return RedirectResponse(url="/login?error=service_unavailable", status_code=302)
    return res


@app.get("/refresh")
def refresh_page(request: Request, refresh_token: str = Cookie(None), db: Session = Depends(get_db)):
    """Browser-navigation refresh: new access cookie, then back to the dashboard.
    (Fetch/XHR callers use POST /auth/refresh instead.)"""
    rate_limit_response = check_rate_limit(key=f"refresh:{client_ip(request)}", limit=20, window=60)
    if rate_limit_response:
        return rate_limit_response

    if not refresh_token:
        return RedirectResponse(url="/login?error=session_missing", status_code=302)

    try:
        user_id = refresh_user_id(refresh_token)
    except RedisError:
        logger.exception("Redis unavailable during token refresh")
        return RedirectResponse(url="/login?error=service_unavailable", status_code=302)

    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if not user or not user.is_active:
        return RedirectResponse(url="/login?error=session_expired", status_code=302)

    res = RedirectResponse(url="/dashboard", status_code=302)
    set_access_cookie(res, create_access_token(user.id))
    return res


@app.get("/login/google")
async def google_login(request: Request):
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        return RedirectResponse(url="/login?error=oauth_failed", status_code=302)

    userdata = token.get("userinfo")
    if not userdata or "email" not in userdata:
        return RedirectResponse(url="/login?error=invalid_userinfo", status_code=302)
    if not userdata.get("email_verified", False):
        return RedirectResponse(url="/login?error=email_unverified", status_code=302)

    email = userdata["email"].strip().lower()
    user = db.query(User).filter(func.lower(User.username) == email).first()
    if user and user.password:
        # A password account already owns this name. Signup does not verify email
        # ownership, so someone could have pre-registered a victim's address; never
        # let a Google login drop into that account.
        return RedirectResponse(url="/login?error=oauth_account_exists", status_code=302)
    if user and not user.is_active:
        return RedirectResponse(url="/login?error=user_not_found", status_code=302)
    if not user:
        user = User(username=email, password=None)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.query(User).filter(func.lower(User.username) == email).first()
            if not user or user.password:
                return RedirectResponse(url="/login?error=oauth_failed", status_code=302)
        else:
            db.refresh(user)

    res = RedirectResponse(url="/dashboard", status_code=302)
    try:
        start_session(res, user.id)
    except RedisError:
        logger.exception("Redis unavailable during Google login")
        return RedirectResponse(url="/login?error=service_unavailable", status_code=302)
    return res


@app.get("/dashboard", response_class=HTMLResponse)
def user_dashboard(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("user_dashboard.html", {
        "request": request,
        "user": current_user
    })


@app.exception_handler(StarletteHTTPException)
async def auth_exception_handler(request: Request, exc):
    is_api = request.url.path.startswith("/api/")
    is_fetch = request.headers.get("x-requested-with") == "XMLHttpRequest"

    # Never redirect API calls, AJAX calls, or non-auth errors (404, 405, ...) to an HTML page
    if is_api or is_fetch or exc.status_code != 401:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc.detail)},
            headers=getattr(exc, "headers", None),
        )

    # Page navigation with an expired/missing access token but a refresh token:
    # renew silently instead of bouncing the user to the login form.
    if request.cookies.get("refresh_token") and str(exc.detail) in ("session_expired", "session_missing"):
        return RedirectResponse(url="/refresh", status_code=302)

    return RedirectResponse(url=f"/login?error={quote(str(exc.detail))}", status_code=302)
