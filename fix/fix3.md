import os
import re

with open("tests/unit/test_selector.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace RequestView with AnthropicRequest
content = content.replace("class TestRequestView(", "class TestAnthropicRequest(")
content = content.replace("rv = RequestView", "from clasp.router.types import AnthropicRequest; rv = AnthropicRequest.from_body")
content = content.replace("rv.needs_thinking", "rv.type.value == 'THINK'")
content = content.replace('rv = AnthropicRequest.from_body({"model": "x", "messages": [], "type": "THINK"})', 'rv = AnthropicRequest.from_body({"model": "x", "messages": [], "request_type": "THINK"})')

# Rewrite _make_pool
pool_replacement = """
def _make_pool(provider_name, keys, rpm=40, tpm=None, soft=0.80, cd=None, caps=None):
    from clasp.config.provider_catalog import ProviderProfile
    caps = caps or {}
    profile = ProviderProfile(
        display_name=provider_name,
        base_url="https://example.com/v1",
        transport="openai_chat",
        rpm_limit=rpm,
        tpm_limit=tpm,
        daily_token_limit=None,
        rpm_soft_threshold=soft,
        cooldown_seconds=5,
        backoff_base_seconds=5,
        supports_tools=caps.get("supports_tools", True),
        supports_vision=caps.get("supports_vision", True),
        supports_thinking=caps.get("supports_thinking", True),
        max_context_tokens=caps.get("max_context_tokens", 200_000),
        tier="free",
        free_tier_note="test",
    )
    return KeyPool(provider_name, keys, profile, cooldown_tracker=cd or CooldownManager())
"""

content = re.sub(r'def _make_pool\(provider_name.*?cooldown_tracker=CooldownManager\(\)\)', pool_replacement.strip(), content, flags=re.DOTALL)

# Add test_select
test_select_code = """
def test_select(request, **kwargs):
    from clasp.router.types import SelectorConfig, ProviderEnableConfig, AnthropicRequest
    from clasp.providers.registry import ProviderRegistry
    from clasp.config.settings import Settings, RoutingConfig, ModelRoutes, ByTypeRoutes
    
    config = SelectorConfig(
        provider_chain=kwargs.get("provider_chain", []),
        providers={
            p: ProviderEnableConfig(enabled=(p in kwargs.get("enabled_providers", [])))
            for p in kwargs.get("provider_chain", []) + list(kwargs.get("provider_instances", {}).keys())
        },
        by_type=kwargs.get("by_type", {})
    )
    
    registry = ProviderRegistry()
    registry._providers = kwargs.get("provider_instances", {})
    cd = kwargs.get("cooldown_store", CooldownManager())
    
    pools = {}
    for p, keys in kwargs.get("provider_keys", {}).items():
        rpm = kwargs.get("rpm", 40)
        soft = kwargs.get("soft", 0.8)
        caps = kwargs.get("provider_capabilities", {}).get(p, _make_caps())
        pools[p] = _make_pool(p, keys, rpm=rpm, soft=soft, caps=caps, cd=cd)
    registry._key_pools = pools
    
    # Expose pools in kwargs for tests that need to manipulate them (like circuit breakers)
    kwargs["_test_pools"] = pools
    
    settings = Settings(routing=RoutingConfig(
        models=ModelRoutes(**{k: v for k, v in kwargs.get("routing_models", {}).items() if k in ["opus", "sonnet", "haiku", "fable", "default"]}),
    ))
    for t in ["opus", "sonnet", "haiku", "fable", "default"]:
        if getattr(settings.routing.models, t, "") == "":
            setattr(settings.routing.models, t, kwargs.get("routing_models", {}).get(t, ""))
    
    if isinstance(request, dict):
        from clasp.api.detect import RequestType
        rt_val = request.get("request_type", "INTERACTIVE").upper()
        if hasattr(RequestType, rt_val):
            rt = getattr(RequestType, rt_val)
        else:
            rt = RequestType.INTERACTIVE
        req = AnthropicRequest(
            body=request,
            type=rt,
            priority=0,
            estimated_tokens=request.get("estimated_tokens", 100),
            needs_tools=bool(request.get("tools")),
            needs_vision=rt == RequestType.VISION,
        )
    else:
        req = request
        
    return run(select(req, exclude=kwargs.get("exclude"), config=config, registry=registry, cooldown_mgr=cd, settings=settings))

"""

content = content.replace("class TestSelectorBasic(", test_select_code + "\nclass TestSelectorBasic(")

# Update all self._select to use test_select
content = re.sub(r'def _select\(self, request, \*\*kwargs\):\s*return run\(select\(request, \*\*kwargs\)\)', 'def _select(self, request, **kwargs):\n        return test_select(request, **kwargs)', content)

# Remove all CooldownStore and CircuitBreakerStore instantiations
content = re.sub(r'cd,\s*cb\s*=\s*CooldownStore\(\),\s*CircuitBreakerStore\(\)\s*', 'cd = CooldownManager()\n        ', content)
content = re.sub(r'cd\s*=\s*CooldownStore\(\)\s*', 'cd = CooldownManager()\n        ', content)
content = re.sub(r'cb\s*=\s*CircuitBreakerStore\(\)\s*', '', content)
content = re.sub(r'cooldown_store=cd,\s*cb_store=cb,?', 'cooldown_store=cd,', content)

# Fix pick_key and health_summary calls in TestKeyPoolRoundRobin
content = re.sub(r'pool\.pick_key\(cooldown_store=cd\)', 'pool.pick_key()', content)
content = re.sub(r'pool\.health_summary\(cooldown_store=cd\)', 'pool.health_summary()', content)
content = content.replace('_make_pool("nim", ["k0", "k1", "k2"], rpm=100)', '_make_pool("nim", ["k0", "k1", "k2"], rpm=100, cd=cd)')
content = content.replace('_make_pool("nim", ["k0", "k1"], rpm=10, soft=0.80)', '_make_pool("nim", ["k0", "k1"], rpm=10, soft=0.80, cd=cd)')
content = content.replace('_make_pool("nim", ["k0", "k1"], rpm=40)', '_make_pool("nim", ["k0", "k1"], rpm=40, cd=cd)')
content = content.replace('_make_pool("nim", ["k0", "k1", "k2"], rpm=40)', '_make_pool("nim", ["k0", "k1", "k2"], rpm=40, cd=cd)')

# Fix circuit breaker test
cb_test_old = """
        pass
        # Trip CB for nim key 0 (3 consecutive 429s)
        breaker = cb.get("nim", 0)
        for _ in range(3):
            breaker.record_429()
"""
cb_test_new = """
        # We need to create the pools FIRST so we can trip the CB before select!
        # But select creates the pools. So let's create a registry first.
        caps = _make_caps()
        pool = _make_pool("nim", ["k0"], rpm=40, caps=caps, cd=cd)
        breaker = pool.circuit_breakers[0]
        for _ in range(3):
            breaker.record_429()
        kwargs = {
            "provider_chain": ["nim", "gemini"],
            "provider_instances": _make_instances("nim", "gemini"),
            "provider_keys": _make_keys(("nim", ["k0"]), ("gemini", ["k1"])),
            "provider_capabilities": {"nim": caps, "gemini": _make_caps()},
            "routing_models": {"default": "some-model"},
            "enabled_providers": {"nim", "gemini"},
            "cooldown_store": cd,
        }
        # Force the test to use our pre-tripped pool
"""
content = re.sub(r'\s*# Trip CB for nim key 0.*?breaker\.record_429\(\)', cb_test_new, content, flags=re.DOTALL)
content = content.replace('result = self._select(\n            request,\n            provider_chain=["nim", "gemini"]', 'kwargs["_premade_pool"] = pool\n        result = self._select(\n            request,\n            provider_chain=["nim", "gemini"]')

# Inject premade pool logic into test_select
content = content.replace("registry._key_pools = pools", """
    registry._key_pools = pools
    if "_premade_pool" in kwargs:
        registry._key_pools["nim"] = kwargs["_premade_pool"]
""")

with open("tests/unit/test_selector.py", "w", encoding="utf-8") as f:
    f.write(content)