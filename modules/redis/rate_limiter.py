from fastapi.responses import RedirectResponse
from modules.redis.redis_client import redis_client

def check_rate_limit(key: str,
                     limit: int = 5,
                     window: int = 60):
    
    current = redis_client.get(key)
    
    if current is None:
        redis_client.set(
            key,
            1,
            ex=window
        )
        
        return
    
    print(int(current), limit)
    
    if int(current) >= limit:
        print("Here")
        
        return RedirectResponse(url="/login?error=too_many_requests", status_code=302)
        
    redis_client.incr(key)