"""
clasp/optimizer/__init__.py
============================
Phase 6 Payload Optimization Orchestrator.

This module wires together `system_prompt.py`, `context_pruner.py`, and 
`payload_filter.py` into a single `optimize()` pipeline called by the proxy lifecycle.
"""
from __future__ import annotations

import copy
from typing import Any

from clasp.config.settings import get_settings
from clasp.config.provider_catalog import PROVIDER_CATALOG
from clasp.optimizer.system_prompt import process_system_prompt
from clasp.optimizer.context_pruner import prune
from clasp.optimizer.payload_filter import strip_blocked_fields


def optimize(request_body: dict[str, Any], provider_name: str | None = None) -> dict[str, Any]:
    """
    Run the request through the optimizer pipeline (Phase 6 Payload Optimization).
    
    Returns a modified shallow/deep copy of the request_body.
    """
    settings = get_settings()
    
    # We mutate a copy so we don't accidentally poison the original request object
    # before caching or routing fallbacks.
    optimized_body = copy.deepcopy(request_body)
    
    # 1. Gather context limits
    max_context_tokens = 0
    if provider_name:
        profile = PROVIDER_CATALOG.get(provider_name)
        if profile and profile.max_context_tokens:
            max_context_tokens = profile.max_context_tokens
        
        # Override with user settings if they exist
        provider_config = settings.providers.get(provider_name)
        if provider_config and provider_config.max_context_tokens is not None:
            max_context_tokens = provider_config.max_context_tokens

    # 2. System Prompt Deduplication and Caching
    # Note: process_system_prompt handles Gemini cache hinting AND small context trimming
    model_slug = optimized_body.get("model", "")
    if settings.optimizer.system_prompt_dedup and "system" in optimized_body and provider_name:
        system_res = process_system_prompt(
            system=optimized_body.get("system"),
            provider_name=provider_name,
            model_slug=model_slug,
            max_context_tokens=max_context_tokens,
        )
        if system_res.was_trimmed:
            optimized_body["system"] = system_res.text
            
    # 3. Context Pruning
    if settings.optimizer.context_pruning.enabled and max_context_tokens > 0 and "messages" in optimized_body:
        prune_config = settings.optimizer.context_pruning
        prune_res = prune(
            messages=optimized_body["messages"],
            max_tokens=max_context_tokens,
            strategy=prune_config.strategy,
            keep_first=prune_config.keep_first,
            keep_last=prune_config.keep_last,
        )
        if prune_res.pruned:
            optimized_body["messages"] = prune_res.messages

    # 4. Payload Filtering (Strip blocked fields)
    if provider_name:
        optimized_body = strip_blocked_fields(optimized_body, provider_name)
        
    return optimized_body
