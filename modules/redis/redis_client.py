import os
import redis
from core.config import settings

redis_host = getattr(settings, "REDIS_HOST", os.getenv("REDIS_HOST", "redis"))
redis_port = int(getattr(settings, "REDIS_PORT", os.getenv("REDIS_PORT", 6379)))
redis_password = getattr(settings, "REDIS_PASSWORD", os.getenv("REDIS_PASSWORD", None))

# Strip https:// or http:// if accidentally passed
if redis_host and "://" in redis_host:
    redis_host = redis_host.split("://")[-1].split("/")[0]

# Upstash and cloud providers require SSL (rediss)
use_ssl = redis_host not in ["redis", "localhost", "127.0.0.1"]

redis_client = redis.Redis(
    host=redis_host,
    port=redis_port,
    password=redis_password if redis_password else None,
    ssl=use_ssl,
    ssl_cert_reqs=None,
    decode_responses=True
)