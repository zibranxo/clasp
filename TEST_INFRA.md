# E2E Test Infra: Clasp Dynamic Model Selector

## Test Philosophy
- Opaque-box, requirement-driven. No dependency on implementation design.
- Methodology: Category-Partition + BVA + Pairwise + Workload Testing.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | Dynamic Model Listing | ORIGINAL_REQUEST R2, PROJECT.md | 5 | 5 | ✓ |
| 2 | Routing of OpenAI Models | ORIGINAL_REQUEST R2/R3, PROJECT.md | 5 | 5 | ✓ |
| 3 | Routing of Gemini Models | ORIGINAL_REQUEST R2/R3, PROJECT.md | 5 | 5 | ✓ |
| 4 | Caching and Latency Checks | ORIGINAL_REQUEST R3, PROJECT.md | 5 | 5 | ✓ |

## Test Architecture
- Test runner: pytest
- Invocation: `uv run pytest tests/integration/test_dynamic_model_routing.py -v`
- Test case format: pytest async test cases using `httpx.ASGITransport` to call the FastAPI app.
- Directory layout: `tests/integration/test_dynamic_model_routing.py`

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Multi-turn developer conversation | Dynamic listing, Gemini routing, state preservation | High |
| 2 | Tool calling developer flow | Dynamic listing, OpenAI routing, tool calls | High |
| 3 | Vision prompt routing flow | OpenAI/Gemini routing, vision capability checking | High |
| 4 | Failover and queueing under rate limit | Routing of OpenAI/Gemini, key exhaustion | High |
| 5 | Full workspace start and chat session | Discovery, token count, trivial probe, and completion | High |

## Coverage Thresholds
- Tier 1: 5 per feature (20 total)
- Tier 2: 5 per feature (20 total)
- Tier 3: pairwise coverage of major feature interactions (4 total)
- Tier 4: 5 realistic application scenarios (5 total)
- Total tests: 49
