import re

with open('clasp/api/service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove _daily_counters and its related functions
content = re.sub(r'_uptime_start = time\.monotonic\(\)\n_daily_counters = \{.*?\}\n', '', content, flags=re.DOTALL)
content = re.sub(r'def _init_p_stats\(pname: str\):.*?_latencies": collections\.deque\(maxlen=100\)\n        \}', '', content, flags=re.DOTALL)
content = re.sub(r'async def get_service_stats\(\) -> dict\[str, Any\]:.*?    return \{\n.*?"providers": providers_status\n    \}', '', content, flags=re.DOTALL)
content = re.sub(r'def record_429_absorbed\(\):.*?_daily_counters\["absorbed_429s_today"\] \+= 1\n', '', content, flags=re.DOTALL)
content = re.sub(r'def record_failover\(\):.*?_daily_counters\["failovers_today"\] \+= 1\n', '', content, flags=re.DOTALL)
content = re.sub(r'def get_daily_counters\(\) -> dict:.*?    return export\n', '', content, flags=re.DOTALL)
content = re.sub(r'def set_daily_counters\(counters: dict\) -> None:.*?_daily_counters\["shared_pool_usage"\] = counters\["shared_pool_usage"\]', '', content, flags=re.DOTALL)

# 2. Add telemetry import
content = content.replace('from clasp.utils.logger import get_logger', 'from clasp.utils.logger import get_logger\nfrom clasp.telemetry import get_telemetry, get_service_stats')

# 3. Replace tracking logic
content = content.replace('_daily_counters["requests_today"] += 1', 'import asyncio\n    asyncio.create_task(get_telemetry().record_active_start())')
content = content.replace('_daily_counters["cache_hits_today"] += 1', 'asyncio.create_task(get_telemetry().record_request(provider=None, cache_hit=True))')

dispatch_track_regex = r'pname = getattr\(provider, "name", getattr\(provider, "provider_name", ""\)\) if provider else ""\n\s*if pname:\n\s*_init_p_stats\(pname\)\n\s*if success:\n\s*latency_ms = round\(\(time\.monotonic\(\) - t0\) \* 1000\)\n\s*_daily_counters\["providers"\]\[pname\]\["_latencies"\]\.append\(latency_ms\)\n\s*_daily_counters\["providers"\]\[pname\]\["requests_today"\] \+= 1\n\s*used_tokens = actual_tokens or estimated_tokens\n\s*_daily_counters\["providers"\]\[pname\]\["tokens_today"\] \+= used_tokens\n\s*_daily_counters\["tokens_today"\] \+= used_tokens\n\s*else:\n\s*_daily_counters\["providers"\]\[pname\]\["errors_today"\] \+= 1'

new_track = '''pname = getattr(provider, "name", getattr(provider, "provider_name", "")) if provider else ""
        if pname:
            if success:
                used_tokens = actual_tokens or estimated_tokens
                await get_telemetry().record_request(
                    provider=pname,
                    tokens=used_tokens,
                    latency_s=time.monotonic() - t0
                )
            else:
                await get_telemetry().record_request(
                    provider=pname,
                    error=True
                )
        await get_telemetry().record_active_end()'''

content = re.sub(dispatch_track_regex, new_track, content, flags=re.DOTALL)

with open('clasp/api/service.py', 'w', encoding='utf-8') as f:
    f.write(content)
