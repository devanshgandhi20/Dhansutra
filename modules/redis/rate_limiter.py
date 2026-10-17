import logging

from fastapi.responses import RedirectResponse
from redis.exceptions import RedisError

from modules.redis.redis_client import redis_client

logger = logging.getLogger(__name__)


def is_rate_limited(key: str, limit: int = 5, window: int = 60) -> bool:
    """Count one hit for `key`; True once more than `limit` hits land inside `window` seconds.

    INCR is atomic, so concurrent requests can no longer slip past the limit
    (the old get/set/incr sequence was racy). If Redis is down we log and let
    the request through rather than locking everyone out.
    """
    redis_key = f"rl:{key}"
    try:
        pipe = redis_client.pipeline()
        pipe.incr(redis_key)
        pipe.ttl(redis_key)
        count, ttl = pipe.execute()
        if ttl == -1:  # first hit, or a key that lost its expiry
            redis_client.expire(redis_key, window)
    except RedisError:
        logger.exception("Rate limiter unavailable; allowing request")
        return False
    return count > limit


def check_rate_limit(key: str, limit: int = 5, window: int = 60):
    """Form-page helper: returns a redirect response when over the limit, else None."""
    if is_rate_limited(key, limit, window):
        return RedirectResponse(url="/login?error=too_many_requests", status_code=302)
    return None
