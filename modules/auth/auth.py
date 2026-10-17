from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt only looks at the first 72 bytes; newer bcrypt releases raise on longer input.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str):
    return pwd_context.hash(password)


def verify_password(plain, hashed):
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        # over-long input or a malformed stored hash: treat as "wrong password", not a 500
        return False
