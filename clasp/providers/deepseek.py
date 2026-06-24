"""
clasp/providers/deepseek.py
===========================
Custom provider implementation for DeepSeek. Inherits from AnthropicMessagesTransport.
"""

from __future__ import annotations

import copy
import time
from typing import AsyncIterator, Any
import httpx
from loguru import logger

from clasp.providers.anthropic_transport import AnthropicMessagesTransport


class DeepSeekProvider(AnthropicMessagesTransport):
    """
    DeepSeek provider class.
    Overrides _stream_raw to sanitize requests and list_models to query the OpenAI-style endpoint.
    """

    def _sanitize_request(self, request: dict | Any) -> dict:
        if isinstance(request, dict):
            payload = copy.deepcopy(request)
        else:
            payload = request.model_dump(exclude_none=True)

        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload

        _STRIPPABLE_MESSAGE_BLOCK_TYPES = frozenset({"image", "document"})

        new_messages = []
        for message in messages:
            if not isinstance(message, dict):
                new_messages.append(message)
                continue

            content = message.get("content")
            if isinstance(content, list):
                new_content = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type")
                        if btype in _STRIPPABLE_MESSAGE_BLOCK_TYPES:
                            continue
                        if btype == "tool_result":
                            inner = block.get("content")
                            if inner is None or inner == "" or inner == []:
                                continue
                            if isinstance(inner, list):
                                filtered_inner = [
                                    sub for sub in inner
                                    if not (isinstance(sub, dict) and sub.get("type") in _STRIPPABLE_MESSAGE_BLOCK_TYPES)
                                ]
                                if not filtered_inner:
                                    continue
                                new_block = dict(block)
                                new_block["content"] = filtered_inner
                                new_content.append(new_block)
                            else:
                                new_content.append(block)
                            continue
                    new_content.append(block)
                new_msg = dict(message)
                new_msg["content"] = new_content
                new_messages.append(new_msg)
            else:
                new_messages.append(message)

        payload["messages"] = new_messages
        return payload

    async def _stream_raw(
        self,
        request: dict | Any,
        key: str,
        key_index: int,
    ) -> AsyncIterator[str]:
        sanitized = self._sanitize_request(request)
        async for chunk in super()._stream_raw(sanitized, key, key_index):
            yield chunk

    async def list_models(self, api_key: str | None = None) -> list[str]:
        if self.static_models is not None:
            return list(self.static_models)

        now = time.monotonic()
        if (
            self._models_cache is not None
            and (now - self._models_cache_time) < self._models_cache_ttl_seconds
        ):
            return self._models_cache

        try:
            url = str(httpx.URL(self.base_url).copy_with(path="/models", query=None, fragment=None))
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            models = [
                entry.get("id", "")
                for entry in data.get("data", [])
                if isinstance(entry, dict) and entry.get("id")
            ]
        except Exception as e:
            logger.warning(f"{self.name}: list_models() failed: {e}")
            return self._models_cache or []

        self._models_cache = models
        self._models_cache_time = now
        return models
