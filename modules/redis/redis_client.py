import os
import redis
from core.config import settings

# 1. Check if a full REDIS_URL or REDISS_URL is provided
redis_url = os.getenv("REDIS_URL")

if redis_url:
    # URL format: rediss://default:password@host:port
    redis_client = redis.from_url(redis_url, decode_responses=True)
else:
    # 2. Fall back to host/port/password
    redis_host = getattr(settings, "REDIS_HOST", os.getenv("REDIS_HOST", "redis"))
    redis_port = int(getattr(settings, "REDIS_PORT", os.getenv("REDIS_PORT", 6379)))
    redis_password = getattr(settings, "REDIS_PASSWORD", os.getenv("REDIS_PASSWORD", None))

    # Strip prefixes or trailing characters
    if redis_host and "://" in redis_host:
        redis_host = redis_host.split("://")[-1].split("/")[0]
    if redis_password:
        redis_password = redis_password.strip()

    is_cloud = redis_host not in ["redis", "localhost", "127.0.0.1"]

    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        username="default" if is_cloud else None,  # Upstash default user
        password=redis_password if redis_password else None,
        ssl=is_cloud,
        ssl_cert_reqs=None,
        decode_responses=True
    )