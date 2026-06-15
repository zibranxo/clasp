# CLASP — Claude API Switching Proxy

## Project
A rate-limit-aware multi-provider proxy for Claude Code. See plan.md for full spec.

## Stack
Python 3.11+, FastAPI, uv, asyncio, loguru, pydantic-settings, ruamel.yaml, tiktoken

## Current sprint
[UPDATE THIS EACH SESSION]
Sprint 1, Step 12/32: implementing providers/common/message_converter.py

## Key rules for this codebase
- All async. Never use blocking I/O or time.sleep().
- Every shared state (buckets, pools, cooldown) uses asyncio.Lock.
- loguru for all logging. Import: from clasp.utils.logger import log
- get_settings() is the singleton. Never instantiate Settings() directly.
- Tests use pytest-asyncio with asyncio_mode=auto.

## Do not touch
- plan.md (reference only, do not modify)
- Tests already written and passing