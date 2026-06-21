import re

with open("tests/unit/test_selector.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update _make_pool signature and usage
# Old: def _make_pool(provider_name, keys, rpm=40, tpm=None, soft=0.80):
# New: def _make_pool(provider_name, keys, rpm=40, tpm=None, soft=0.80, cd=None, caps=None):
content = re.sub(
    r'def _make_pool\(provider_name, keys, rpm=40, tpm=None, soft=0.80\):',
    'def _make_pool(provider_name, keys, rpm=40, tpm=None, soft=0.80, cd=None, caps=None):',
    content
)
# Update ProviderProfile creation inside _make_pool
content = re.sub(
    r'supports_tools=True,\s*supports_vision=True,\s*supports_thinking=True,\s*max_context_tokens=200_000,',
    'supports_tools=caps.get("supports_tools", True) if caps else True,\n        supports_vision=caps.get("supports_vision", True) if caps else True,\n        supports_thinking=caps.get("supports_thinking", True) if caps else True,\n        max_context_tokens=caps.get("max_context_tokens", 200_000) if caps else 200_000,',
    content
)
# Update KeyPool instantiation inside _make_pool
content = re.sub(
    r'return KeyPool\(provider_name, keys, profile, cooldown_tracker=CooldownManager\(\)\)',
    'return KeyPool(provider_name, keys, profile, cooldown_tracker=cd or CooldownManager())',
    content
)

# 2. Update test_select to use the new _make_pool args and remove frozen assignment
new_test_select_pool_creation = """
        caps = kwargs.get("provider_capabilities", {}).get(p, _make_caps())
        pools[p] = _make_pool(p, keys, rpm=rpm, soft=soft, caps=caps, cd=cd)
"""
content = re.sub(
    r'pools\[p\] = _make_pool\(p, keys, rpm=rpm, soft=soft\).*?pools\[p\]\.profile\.max_context_tokens = caps\.get\("max_context_tokens", 200_000\)',
    new_test_select_pool_creation.strip(),
    content,
    flags=re.DOTALL
)

# 3. Add AnthropicRequest import
if "from clasp.router.types import AnthropicRequest" not in content:
    content = content.replace("from clasp.router.selector import select", "from clasp.router.selector import select\nfrom clasp.router.types import AnthropicRequest")

# 4. Fix test_returns_none_when_all_keys_cooling
# pool = _make_pool("nim", ["k0", "k1"], rpm=40) -> pool = _make_pool("nim", ["k0", "k1"], rpm=40, cd=cd)
content = content.replace('pool = _make_pool("nim", ["k0", "k1"], rpm=40)', 'pool = _make_pool("nim", ["k0", "k1"], rpm=40, cd=cd)')
content = content.replace('pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=40)', 'pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=40, cd=cd)')
content = content.replace('pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=100)', 'pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=100, cd=cd)')
content = content.replace('pool = _make_pool("nim", ["k0", "k1"], rpm=10, soft=0.80)', 'pool = _make_pool("nim", ["k0", "k1"], rpm=10, soft=0.80, cd=cd)')

with open("tests/unit/test_selector.py", "w", encoding="utf-8") as f:
    f.write(content)