from fastapi import FastAPI, Form, Request, Response, Depends, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from urllib.parse import quote
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from core.database import engine, Base
from core.google_auth import oauth
from core.config import settings
from modules.users.models import User
from modules.auth.router import router as auth_router
from modules.auth.auth import hash_password, verify_password
from modules.auth.jwtutils import create_access_token, create_refresh_token, verify_refresh_token
from modules.auth.userverification import get_db, get_current_user
from modules.redis.redis_client import redis_client
from modules.redis.rate_limiter import check_rate_limit
from modules.finance.router import router as finance_router
from modules.finance.imports import router as imports_router

templates = Jinja2Templates(directory="templates")

app = FastAPI(title="DhanSutra API")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY
)

app.include_router(auth_router)
app.include_router(finance_router)
app.include_router(imports_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/create_user")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...), 
    db: Session = Depends(get_db)
):
    existing_user = db.query(User).filter(User.username == username).first()
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
    db.commit()
    db.refresh(new_user)
    return RedirectResponse(url="/login?success=user_created", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = None, success: str = None):
    error_message = None
    success_message = None
    
    if error == "1":
        error_message = "Invalid credentials"
    elif error == "session_expired":
        error_message = "Session timed out. Please login again."
    elif error == "too_many_requests":
        error_message = "Too many requests. Please try again later."
    elif error == "session_missing":
        error_message = "Please login first."
        
    if success == "user_created":
        success_message = "Account created successfully."
    elif success == "password_reset":
        success_message = "Password updated successfully."
        
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error_message, "success": success_message}
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
    db: Session = Depends(get_db)
):
    ip = request.client.host
    rate_limit_response = check_rate_limit(key=f"forgot:{username}:{ip}", limit=3, window=300)
    if rate_limit_response:
        return rate_limit_response
        
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return RedirectResponse(url="/forgot_password?error=No user found with this username", status_code=302)
        
    user.password = hash_password(password)
    db.commit()
    return RedirectResponse(url="/login?success=password_reset", status_code=302)

@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    ip = request.client.host
    rate_limit_response = check_rate_limit(key=f"login:{username}:{ip}", limit=5, window=60)
    if rate_limit_response:
        return rate_limit_response
        
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password or not verify_password(password, user.password):
        return RedirectResponse(url="/login?error=1", status_code=302)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    redis_client.set(f"refresh:{user.id}", refresh_token, ex=86400 * settings.REFRESH_TOKEN_EXPIRE_DAYS)

    res = RedirectResponse(url="/dashboard", status_code=302)
    res.set_cookie(key="access_token", value=access_token, httponly=True)
    res.set_cookie(key="refresh_token", value=refresh_token, httponly=True)
    return res

@app.get("/refresh")
def refresh_token(request: Request, refresh_token: str = Cookie(None)):
    ip = request.client.host
    rate_limit_response = check_rate_limit(key=f"refresh:{ip}", limit=20, window=60)
    if rate_limit_response:
        return rate_limit_response

    if not refresh_token:
        return RedirectResponse(url="/login?error=session_missing")

    payload = verify_refresh_token(refresh_token)
    if not payload or "sub" not in payload:
        return RedirectResponse(url="/login?error=session_expired")

    user_id = payload["sub"]
    saved_token = redis_client.get(f"refresh:{user_id}")
    if saved_token != refresh_token:
        return RedirectResponse(url="/login?error=session_expired")

    new_access_token = create_access_token(int(user_id))
    res = RedirectResponse(url="/dashboard")
    res.set_cookie(key="access_token", value=new_access_token, httponly=True)
    return res

@app.get("/login/google")
async def google_login(request: Request):
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    userdata = token["userinfo"]
    email = userdata["email"]

    user = db.query(User).filter(User.username == email).first()
    if not user:
        user = User(username=email, password=None)
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    redis_client.set(f"refresh:{user.id}", refresh_token, ex=86400 * settings.REFRESH_TOKEN_EXPIRE_DAYS)

    res = RedirectResponse(url="/dashboard", status_code=302)
    res.set_cookie(key="access_token", value=access_token, httponly=True)
    res.set_cookie(key="refresh_token", value=refresh_token, httponly=True)
    return res

@app.get("/dashboard", response_class=HTMLResponse)
def user_dashboard(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("user_dashboard.html", {
        "request": request,
        "user": current_user
    })

@app.exception_handler(StarletteHTTPException)
async def auth_exception_handler(request: Request, exc):
    is_fetch = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if exc.status_code in (401, 403) and is_fetch:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    if exc.status_code == 401:
        return RedirectResponse(url=f"/login?error={exc.detail}")
    return RedirectResponse(url="/login")