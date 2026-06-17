"""
clasp/providers/registry.py
============================
Provider registry: maps provider names to instantiated ``BaseProvider`` objects.

Sprint 1 responsibilities
--------------------------
- Maintain a dict of ``{provider_name → BaseProvider}`` for all **enabled** providers
  declared in ``config.yaml``.
- Expose a module-level singleton (``_registry``) that is built once at startup via
  :func:`build_registry` and then read by :func:`get` / :func:`get_key_pool` /
  :func:`all_enabled`.
- Expose ``get_key_pool(name)`` as a **stub** that always returns ``None`` for Sprint 1.
  Sprint 2 (step 38) replaces this with real ``KeyPool`` instantiation.

Registry class
--------------
``ProviderRegistry``
    Built by :func:`build_registry`.  Holds:
    - ``_providers: dict[str, BaseProvider]`` — live instances.
    - ``_key_pools: dict[str, Any]`` — filled in Sprint 2; empty for now.

Module-level helpers
---------------------
``build_registry(settings)``
    Iterate ``settings.provider_chain``.  For each provider name that is enabled
    and has ≥1 key, instantiate the correct transport class and register it.
    Logs unknown provider names at WARNING level.  Unknown names are skipped
    (not a fatal error — allows adding new providers to config before code ships).

``get(name) → BaseProvider | None``
    Return the registered provider, or ``None`` if unknown/disabled.

``get_key_pool(name) → None``
    Sprint 1 stub.  Returns ``None`` unconditionally.
    Sprint 2 replaces: ``return _registry._key_pools.get(name)``.

``all_enabled() → list[str]``
    Names of all registered providers, in registration order.

``rebuild(settings)``
    Tear down existing registry and rebuild from fresh settings.
    Called by the config watcher hot-reload callback.

Transport class lookup
-----------------------
``PROVIDER_CLASS_MAP`` maps provider name strings (matching ``PROVIDER_CATALOG`` keys)
to ``(class, extra_kwargs)`` tuples.  All classes receive ``base_url`` from the catalog
as a keyword argument.

Provider classes for providers not yet implemented in Sprint 1 are mapped to the
appropriate transport base class using the catalog's ``transport`` field as a fallback.

References: plan.md §9 (step 18), §13 (selector uses registry.get / get_key_pool),
            CLAUDE.md "Files completed so far".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.registry")

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg + ("  " + str(kw) if kw else ""))

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def error(self, msg: str, **kw: Any) -> None:
            self._log.error(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.config.provider_catalog import PROVIDER_CATALOG
from clasp.providers.base import BaseProvider
from clasp.providers.openai_transport import OpenAIChatTransport
from clasp.providers.anthropic_transport import AnthropicMessagesTransport
from clasp.providers.nvidia_nim import NvidiaProvider

if TYPE_CHECKING:
    from clasp.config.settings import Settings

# ---------------------------------------------------------------------------
# Provider class map
# ---------------------------------------------------------------------------
# Maps catalog key → concrete class.
# Providers not yet implemented fall back to the generic transport class
# matching catalog["transport"].  Sprint 4 adds concrete classes for all.
# ---------------------------------------------------------------------------

PROVIDER_CLASS_MAP: dict[str, type[BaseProvider]] = {
    "nvidia_nim": NvidiaProvider,
    # Sprint 4 providers — use generic transports until concrete classes land:
    "gemini": OpenAIChatTransport,
    "cerebras": OpenAIChatTransport,
    "groq": OpenAIChatTransport,
    "fireworks": AnthropicMessagesTransport,
    "openrouter": OpenAIChatTransport,
    "mistral": OpenAIChatTransport,
    "together": OpenAIChatTransport,
    "ollama": OpenAIChatTransport,
    "lm_studio": OpenAIChatTransport,
}

# ---------------------------------------------------------------------------
# Transport fallback resolver
# ---------------------------------------------------------------------------

def _transport_class(provider_name: str) -> type[BaseProvider]:
    """
    Return the concrete class to use for *provider_name*.

    1. Check ``PROVIDER_CLASS_MAP`` for an explicit mapping.
    2. Fall back to the generic transport class derived from the catalog's
       ``transport`` field (``"openai_chat"`` or ``"anthropic_messages"``).
    3. If the provider is unknown to both, fall back to ``OpenAIChatTransport``
       and log a warning.
    """
    if provider_name in PROVIDER_CLASS_MAP:
        return PROVIDER_CLASS_MAP[provider_name]

    profile = PROVIDER_CATALOG.get(provider_name)
    if profile is None:
        logger.warning(
            "registry: unknown provider — defaulting to OpenAIChatTransport",
            provider=provider_name,
        )
        return OpenAIChatTransport

    if profile.transport == "anthropic_messages":
        return AnthropicMessagesTransport
    return OpenAIChatTransport


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """
    Holds live provider instances.  Built once at startup by :func:`build_registry`.

    Attributes
    ----------
    _providers:
        Maps ``provider_name → BaseProvider`` for all successfully instantiated
        enabled providers.
    _key_pools:
        Sprint 1: always empty.  Sprint 2 populates this with ``KeyPool`` instances.
    _registration_order:
        Preserves the order providers were registered (matches ``provider_chain``).
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._key_pools: dict[str, Any] = {}           # populated in Sprint 2
        self._registration_order: list[str] = []

    # ------------------------------------------------------------------
    # Write interface (used only by build_registry / rebuild)
    # ------------------------------------------------------------------

    def _register(self, name: str, instance: BaseProvider) -> None:
        self._providers[name] = instance
        if name not in self._registration_order:
            self._registration_order.append(name)
        logger.debug("registry: registered provider", provider=name,
                     cls=type(instance).__name__)

    def _clear(self) -> None:
        self._providers.clear()
        self._key_pools.clear()
        self._registration_order.clear()

    # ------------------------------------------------------------------
    # Read interface
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseProvider | None:
        """Return the provider instance, or ``None`` if not registered."""
        return self._providers.get(name)

    def get_key_pool(self, name: str) -> Any | None:
        """
        Return the ``KeyPool`` for *name*, or ``None``.

        Sprint 1: always returns ``None`` — key pools are introduced in Sprint 2.
        """
        return self._key_pools.get(name)

    def all_enabled(self) -> list[str]:
        """Names of all registered providers, in the order they were registered."""
        return list(self._registration_order)

    def __len__(self) -> int:
        return len(self._providers)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ProviderRegistry(providers={self._registration_order!r})"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry = ProviderRegistry()


# ---------------------------------------------------------------------------
# Public build / query helpers
# ---------------------------------------------------------------------------

def build_registry(settings: "Settings") -> ProviderRegistry:
    """
    Instantiate enabled providers from *settings* and register them.

    Iterates ``settings.provider_chain`` in order so the registry reflects the
    user's preferred failover sequence.  Only providers that are:

    1. Listed in ``settings.provider_chain``.
    2. Present in ``settings.providers`` and have ``enabled = True``.
    3. Have at least one API key configured (or are local providers with no key
       requirement — identified by ``tier == "local"``).

    are instantiated.  All other providers are silently skipped so a typo in
    ``config.yaml`` never prevents the server from starting.

    Parameters
    ----------
    settings:
        Loaded and validated ``Settings`` object.

    Returns
    -------
    ProviderRegistry
        The now-populated module-level singleton (same object as ``_registry``).
    """
    global _registry
    _registry._clear()

    for provider_name in settings.provider_chain:
        provider_cfg = settings.providers.get(provider_name)
        if provider_cfg is None:
            logger.debug(
                "registry: provider in chain has no config, skipping",
                provider=provider_name,
            )
            continue

        if not provider_cfg.enabled:
            logger.debug(
                "registry: provider disabled, skipping",
                provider=provider_name,
            )
            continue

        # Local providers (Ollama, LM Studio) don't require API keys.
        catalog_profile = PROVIDER_CATALOG.get(provider_name)
        is_local = catalog_profile is not None and catalog_profile.tier == "local"
        has_keys = bool(provider_cfg.keys)

        if not has_keys and not is_local:
            logger.warning(
                "registry: provider enabled but has no API keys — skipping",
                provider=provider_name,
                hint="Add at least one key in the UI or config.yaml",
            )
            continue

        # Resolve and instantiate the provider class.
        cls = _transport_class(provider_name)
        try:
            # Prefer passing base_url from catalog if the constructor accepts it.
            kwargs: dict[str, Any] = {}
            if catalog_profile is not None:
                kwargs["base_url"] = catalog_profile.base_url

            instance = cls(**kwargs)
            _registry._register(provider_name, instance)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "registry: failed to instantiate provider — skipping",
                provider=provider_name,
                cls=cls.__name__,
                error=str(exc),
            )
            continue

    logger.info(
        "registry: build complete",
        registered=_registry.all_enabled(),
        total=len(_registry),
    )
    return _registry


def get(name: str) -> BaseProvider | None:
    """Return the provider instance for *name*, or ``None``."""
    return _registry.get(name)


def get_key_pool(name: str) -> Any | None:
    """
    Return the ``KeyPool`` for *name*.

    Sprint 1 stub — always returns ``None``.
    Updated in Sprint 2 step 38 to return real ``KeyPool`` objects.
    """
    return _registry.get_key_pool(name)


def all_enabled() -> list[str]:
    """Names of all registered (enabled) providers in registration order."""
    return _registry.all_enabled()


def rebuild(settings: "Settings") -> ProviderRegistry:
    """
    Hot-reload: teardown and rebuild the registry from refreshed *settings*.

    Called by ``config/watcher.py`` after a config change is detected.
    Thread-safe for reads that happen concurrently — the module-level ``_registry``
    object is replaced atomically (CPython GIL) once the new one is fully built.
    """
    logger.info("registry: rebuilding from updated settings")
    return build_registry(settings)