import asyncio
from clasp.config.settings import get_settings
from clasp.cache.semantic_cache import get_semantic_cache

async def test():
    settings = get_settings()
    settings.cache.semantic_enabled = True
    
    cache = get_semantic_cache()
    
    q1 = {
        "messages": [{"role": "user", "content": "Write a Python script to reverse a list"}]
    }
    
    q2 = {
        "messages": [{"role": "user", "content": "Give me python code to reverse a list"}]
    }
    
    fake_sse = [b"event: message\ndata: {\"text\": \"Here is your script...\"}"]
    
    print("Looking up q1 before storing...")
    res = await cache.lookup(q1)
    print("q1 hit:", res is not None)
    
    print("Storing q1 with fake SSE payload...")
    await cache.store(q1, fake_sse)
    
    print("Looking up q2 (which is semantically similar to q1)...")
    res2 = await cache.lookup(q2)
    print("q2 hit:", res2 is not None)
    if res2:
        print("Data retrieved:", res2)

asyncio.run(test())
