from jose import jwt, JWTError
from datetime import datetime, timedelta
from core.config import settings

SECRET_KEY = settings.SECRET_KEY
SECRET_REFRESH_KEY = settings.SECRET_REFRESH_KEY
ALGORITHM = settings.ALGORITHM

def create_access_token(user_id: int):
    to_encode = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: int):
    to_encode = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    }
    return jwt.encode(to_encode, SECRET_REFRESH_KEY, algorithm=ALGORITHM)

def verify_access_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def verify_refresh_token(token: str):
    try:
        return jwt.decode(token, SECRET_REFRESH_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None