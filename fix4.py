with open("tests/unit/test_selector.py", "r", encoding="utf-8") as f:
    content = f.read()

# Only test_skips_open_circuit_breaker actually has `pool` defined.
# I'll just remove kwargs["_premade_pool"] = pool globally, and put it back explicitly in test_skips_open_circuit_breaker.

content = content.replace('kwargs["_premade_pool"] = pool\n        result = self._select(', 'result = self._select(')

# In test_skips_open_circuit_breaker
cb_setup = """
        kwargs = {
            "provider_chain": ["nim", "gemini"],
"""
cb_fixed = """
        kwargs = {
            "_premade_pool": pool,
            "provider_chain": ["nim", "gemini"],
"""
content = content.replace(cb_setup, cb_fixed)

with open("tests/unit/test_selector.py", "w", encoding="utf-8") as f:
    f.write(content)
