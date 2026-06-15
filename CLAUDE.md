# CLASP — Claude API Switching Proxy

## What this project is
A rate-limit-aware multi-provider proxy. Lets Claude Code use free APIs 
(NVIDIA NIM, Gemini, Groq, etc.) without hitting 429 errors.
Full spec is in plan.md. Read Section 20 for file implementation order.

## Tech stack
Python 3.11, FastAPI, uv, asyncio, loguru, pydantic-settings, 
ruamel.yaml, tiktoken, httpx, rich, aiosqlite, watchfiles, typer

## Rules you must follow
- ALL code is async. Never use time.sleep(), always await asyncio.sleep().
- Every shared mutable state uses asyncio.Lock.
- Use loguru for logging: from clasp.utils.logger import log
- Use get_settings() singleton. Never instantiate Settings() directly.
- Use pathlib.Path everywhere, never string concatenation for paths.
- On Windows, use subprocess.run() instead of os.execvp() in cmd_claude.py.
- Tests: pytest-asyncio with asyncio_mode=auto in pytest.ini.

## Current sprint and step
Sprint 1 — Step 1 of 32
Currently implementing: pyproject.toml entry points

## Files completed so far
(none yet)

## Do NOT modify
- plan.md  (it is the spec, read-only reference)
- Any test file that is already passing