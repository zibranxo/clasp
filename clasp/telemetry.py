import collections
import statistics
import time
from typing import Any
import asyncio

class ProviderTelemetry:
    def __init__(self):
        self.requests_today = 0
        self.tokens_today = 0
        self.errors_today = 0
        self.absorbed_429s_today = 0
        self.failovers_today = 0
        self.cache_hits_today = 0
        self.latencies = collections.deque(maxlen=100)

    @property
    def p50_latency_ms(self) -> int | None:
        if not self.latencies:
            return None
        return int(statistics.median(self.latencies) * 1000)


class TelemetryManager:
    def __init__(self):
        self.startup_time = time.time()
        self.global_requests = 0
        self.global_tokens = 0
        self.global_errors = 0
        self.global_429s = 0
        self.global_failovers = 0
        self.global_cache_hits = 0
        self.active_requests = 0
        
        self.providers: dict[str, ProviderTelemetry] = collections.defaultdict(ProviderTelemetry)
        self._lock = asyncio.Lock()

    async def record_active_start(self):
        async with self._lock:
            self.active_requests += 1

    async def record_active_end(self):
        async with self._lock:
            self.active_requests = max(0, self.active_requests - 1)

    async def record_request(
        self,
        provider: str | None,
        tokens: int = 0,
        latency_s: float | None = None,
        error: bool = False,
        cache_hit: bool = False,
        is_429: bool = False,
        is_failover: bool = False,
    ):
        async with self._lock:
            if not cache_hit and not is_429 and not is_failover:
                self.global_requests += 1
                if provider:
                    self.providers[provider].requests_today += 1
            
            if tokens:
                self.global_tokens += tokens
                if provider:
                    self.providers[provider].tokens_today += tokens
                    
            if error:
                self.global_errors += 1
                if provider:
                    self.providers[provider].errors_today += 1
                    
            if cache_hit:
                self.global_cache_hits += 1
                if provider:
                    self.providers[provider].cache_hits_today += 1
                    
            if is_429:
                self.global_429s += 1
                if provider:
                    self.providers[provider].absorbed_429s_today += 1
                    
            if is_failover:
                self.global_failovers += 1
                if provider:
                    self.providers[provider].failovers_today += 1
                    
            if latency_s is not None and provider:
                self.providers[provider].latencies.append(latency_s)

_telemetry_instance = TelemetryManager()

def get_telemetry() -> TelemetryManager:
    return _telemetry_instance

async def get_service_stats() -> dict[str, Any]:
    from clasp.config.settings import get_settings
    from clasp.providers.registry import get_registry
    
    try:
        from clasp.queue.manager import get_queue_manager
        q_mgr = get_queue_manager()
        q_stats = q_mgr.get_queue_stats() if hasattr(q_mgr, "get_queue_stats") else {
            "depth": q_mgr.depth if hasattr(q_mgr, "depth") else 0,
            "interactive": 0,
            "background": 0
        }
    except Exception:
        q_stats = {"depth": 0, "interactive": 0, "background": 0}

    settings = get_settings()
    registry = get_registry()
    
    tm = get_telemetry()
    providers_status = {}

    for name, pcfg in settings.providers.items():
        p_stats = tm.providers[name]
        p50 = p_stats.p50_latency_ms

        if not pcfg.enabled:
            status = "OFF"
            keys_info = []
        else:
            kp = registry.get_key_pool(name)
            if kp:
                h_sum = await kp.health_summary()
                keys_info = h_sum.get("keys", [])
                
                if h_sum.get("healthy", 0) > 0:
                    status = "HEALTHY"
                else:
                    has_open = any(k.get("status") == "circuit_open" for k in keys_info)
                    status = "CIRCUIT_OPEN" if has_open else "COOLING_DOWN"
            else:
                status = "OFF"
                keys_info = []

        providers_status[name] = {
            "status": status,
            "requests_today": p_stats.requests_today,
            "tokens_today": p_stats.tokens_today,
            "daily_token_limit": getattr(pcfg, "daily_token_limit", None),
            "errors_today": p_stats.errors_today,
            "p50_latency_ms": p50,
            "keys": keys_info
        }

    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - tm.startup_time),
        "active_requests": tm.active_requests,
        "queue_depth": q_stats.get("depth", 0),
        "queue_interactive": q_stats.get("interactive", 0),
        "queue_background": q_stats.get("background", 0),
        "requests_today": tm.global_requests,
        "tokens_today": tm.global_tokens,
        "absorbed_429s_today": tm.global_429s,
        "failovers_today": tm.global_failovers,
        "cache_hits_today": tm.global_cache_hits,
        "providers": providers_status
    }

def get_daily_counters() -> dict:
    tm = get_telemetry()
    export = {
        "requests_today": tm.global_requests,
        "tokens_today": tm.global_tokens,
        "absorbed_429s_today": tm.global_429s,
        "failovers_today": tm.global_failovers,
        "cache_hits_today": tm.global_cache_hits,
        "providers": {}
    }
    for name, pstats in tm.providers.items():
        export["providers"][name] = {
            "requests_today": pstats.requests_today,
            "tokens_today": pstats.tokens_today,
            "errors_today": pstats.errors_today
        }
        
    try:
        from clasp.config.settings import get_settings
        from clasp.ratelimit.shared_pool import get_shared_pool
        settings = get_settings()
        if settings.shared_pool.enabled:
            pool = get_shared_pool(settings.shared_pool.per_user_rpm_limit)
            export["shared_pool_usage"] = pool.get_daily_usage()
    except Exception:
        pass
        
    return export

def set_daily_counters(counters: dict) -> None:
    if not counters:
        return
    tm = get_telemetry()
    tm.global_requests = counters.get("requests_today", 0)
    tm.global_tokens = counters.get("tokens_today", 0)
    tm.global_429s = counters.get("absorbed_429s_today", 0)
    tm.global_failovers = counters.get("failovers_today", 0)
    tm.global_cache_hits = counters.get("cache_hits_today", 0)
    
    for name, pstats in counters.get("providers", {}).items():
        tm.providers[name].requests_today = pstats.get("requests_today", 0)
        tm.providers[name].tokens_today = pstats.get("tokens_today", 0)
        tm.providers[name].errors_today = pstats.get("errors_today", 0)
        
    if "shared_pool_usage" in counters:
        try:
            from clasp.config.settings import get_settings
            from clasp.ratelimit.shared_pool import get_shared_pool
            settings = get_settings()
            if settings.shared_pool.enabled:
                pool = get_shared_pool(settings.shared_pool.per_user_rpm_limit)
                pool.set_daily_usage(counters["shared_pool_usage"])
        except Exception:
            pass
