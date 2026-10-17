import redis
from core.config import settings

# Fail fast instead of hanging a request when Redis is unreachable.
_COMMON = dict(
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
)

if settings.REDIS_URL:
    # URL format: rediss://default:password@host:port
    redis_client = redis.from_url(settings.REDIS_URL, **_COMMON)
else:
    # Fall back to host/port/password
    redis_host = settings.REDIS_HOST
    redis_port = settings.REDIS_PORT
    redis_password = settings.REDIS_PASSWORD

    # Strip prefixes or trailing characters
    if redis_host and "://" in redis_host:
        redis_host = redis_host.split("://")[-1].split("/")[0]
    if redis_password:
        redis_password = redis_password.strip()

    is_cloud = redis_host not in ["redis", "localhost", "127.0.0.1"]

    tls_options = {"ssl": True, "ssl_cert_reqs": "required"} if is_cloud else {}

    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        username="default" if is_cloud else None,  # Upstash default user
        password=redis_password if redis_password else None,
        **tls_options,
        **_COMMON,
    )
