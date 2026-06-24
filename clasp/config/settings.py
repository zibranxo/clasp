"""
clasp/config/settings.py

Pydantic-Settings v2 model that loads config from ~/.clasp/config.yaml,
then lets environment variables override individual fields.

Usage
-----
    from clasp.config.settings import get_settings

    cfg = get_settings()               # always returns the same object
    print(cfg.server.port)             # 8082

Hot-reload
----------
Call ``get_settings.cache_clear()`` (done by ``config.watcher``) then call
``get_settings()`` again to pick up changes from disk.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# ---------------------------------------------------------------------------
# Config file location
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path.home() / ".clasp" / "config.yaml"


def _get_config_path() -> Path:
    """Return the active config file path, honouring CLASP_CONFIG_PATH."""
    return Path(os.environ.get("CLASP_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH)))


# ---------------------------------------------------------------------------
# Custom YAML settings source
# ---------------------------------------------------------------------------

class _YamlConfigSource(PydanticBaseSettingsSource):
    """
    Pydantic-Settings source that reads ``~/.clasp/config.yaml``.

    The entire YAML document is returned as a flat dict keyed by top-level
    field names; nested models are handled by Pydantic's usual coercion.
    If the file does not exist yet (first run) an empty dict is returned so
    that all defaults kick in.
    """

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        config_path = _get_config_path()
        if not config_path.exists():
            return {}
        try:
            from ruamel.yaml import YAML  # lazy import — avoids hard startup dep
            yaml = YAML()
            raw = yaml.load(config_path)
            return dict(raw) if raw else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to parse config file", path=str(config_path), error=str(exc))
            return {}

    # -- PydanticBaseSettingsSource interface --------------------------------

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        value = self._data.get(field_name)
        return value, field_name, isinstance(value, (dict, list))

    def field_is_required(self, field: Any, field_name: str) -> bool:  # type: ignore[override]
        return False

    def prepare_field_value(
        self,
        field_name: str,
        field: Any,
        value: Any,
        value_is_complex: bool,
    ) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        return self._data


# ---------------------------------------------------------------------------
# Nested config models
# ---------------------------------------------------------------------------

class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8082
    api_key: str = "freecc"
    """Bearer token that Claude Code sends in the Authorization header."""
    request_timeout_seconds: int = 300
    max_queue_depth: int = 50
    max_queue_wait_seconds: int = 180
    log_level: str = "INFO"
    live_tui: bool = False


DEFAULT_ROUTING_MODELS = {
    "opus": "nvidia_nim/moonshotai/kimi-k2-thinking",
    "sonnet": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
    "haiku": "cerebras/llama3.1-8b",
    "fable": "gemini/models/gemini-2.5-flash",
    "default": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
}

DEFAULT_ROUTING_BY_TYPE = {
    "think": "nvidia_nim/moonshotai/kimi-k2-thinking",
    "long_context": "gemini/models/gemini-2.5-flash",
    "background": "groq/llama-3.3-70b-versatile",
    "vision": "openrouter/google/gemini-2.5-flash-preview:free",
}

class ModelRoutes(BaseModel):
    """Maps Claude tier names to ``provider/model-slug`` strings."""
    opus: str = DEFAULT_ROUTING_MODELS["opus"]
    sonnet: str = DEFAULT_ROUTING_MODELS["sonnet"]
    haiku: str = DEFAULT_ROUTING_MODELS["haiku"]
    fable: str = DEFAULT_ROUTING_MODELS["fable"]
    default: str = DEFAULT_ROUTING_MODELS["default"]


class ByTypeRoutes(BaseModel):
    """Optional per-request-type overrides."""
    think: str = DEFAULT_ROUTING_BY_TYPE["think"]
    long_context: str = DEFAULT_ROUTING_BY_TYPE["long_context"]
    background: str = DEFAULT_ROUTING_BY_TYPE["background"]
    vision: str = DEFAULT_ROUTING_BY_TYPE["vision"]


class RoutingConfig(BaseModel):
    strategy: str = "priority-chain"
    """priority-chain | least-loaded | cost-aware"""
    models: ModelRoutes = Field(default_factory=ModelRoutes)
    by_type: ByTypeRoutes = Field(default_factory=ByTypeRoutes)


class ProviderConfig(BaseModel):
    enabled: bool = False
    keys: list[str] = Field(default_factory=list)
    base_url: str | None = None
    """Per-provider base URL override (used by ollama / lm_studio)."""
    rpm_limit: int | None = None
    """Override catalog default if set."""
    tpm_limit: int | None = None
    """Override catalog default if set. `None` means "use the catalog value"
    (which may itself be `None` = unlimited, e.g. nvidia_nim)."""
    soft_threshold_pct: int | None = None
    """0-100; override catalog rpm_soft_threshold if set."""
    max_context_tokens: int | None = None
    """Override catalog default if set. Read by `router/capability.py`."""
    supports_tools: bool | None = None
    """Override catalog default if set. Read by `router/capability.py`."""
    supports_vision: bool | None = None
    """Override catalog default if set. Read by `router/capability.py`."""
    supports_thinking: bool | None = None
    """Override catalog default if set. Read by `router/capability.py`."""


class CacheConfig(BaseModel):
    enabled: bool = True
    memory_max_entries: int = 500
    sqlite_ttl_seconds: int = 300
    sqlite_path: str = "~/.clasp/cache.db"


class ContextPruningConfig(BaseModel):
    enabled: bool = True
    strategy: str = "keep_edges"
    """keep_edges | summarize | truncate"""
    keep_first: int = 3
    keep_last: int = 10


class OptimizerConfig(BaseModel):
    local_probe_answering: bool = True
    system_prompt_dedup: bool = True
    context_pruning: ContextPruningConfig = Field(default_factory=ContextPruningConfig)


class SharedPoolConfig(BaseModel):
    enabled: bool = False
    auth_tokens: list[str] = Field(default_factory=list)
    per_user_rpm_limit: int = 20


# ---------------------------------------------------------------------------
# Default provider configs (all disabled; user enables via UI)
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER_CHAIN: list[str] = [
    "nvidia_nim",
    "gemini",
    "cerebras",
    "groq",
    "openrouter",
    "ollama",
    "mistral_codestral",
    "deepseek",
    "kimi",
    "llamacpp",
    "opencode",
    "opencode_go",
    "wafer",
    "zai",
]

_DEFAULT_PROVIDERS: dict[str, ProviderConfig] = {
    "nvidia_nim":  ProviderConfig(enabled=False),
    "gemini":      ProviderConfig(enabled=False),
    "cerebras":    ProviderConfig(enabled=False),
    "groq":        ProviderConfig(enabled=False),
    "fireworks":   ProviderConfig(enabled=False),
    "openrouter":  ProviderConfig(enabled=False),
    "mistral":     ProviderConfig(enabled=False),
    "together":    ProviderConfig(enabled=False),
    "ollama":      ProviderConfig(enabled=True, base_url="http://localhost:11434"),
    "lm_studio":   ProviderConfig(enabled=False, base_url="http://localhost:1234"),
    "mistral_codestral": ProviderConfig(enabled=False),
    "deepseek":    ProviderConfig(enabled=False),
    "kimi":        ProviderConfig(enabled=False),
    "llamacpp":    ProviderConfig(enabled=False, base_url="http://localhost:8080/v1"),
    "opencode":    ProviderConfig(enabled=False),
    "opencode_go": ProviderConfig(enabled=False),
    "wafer":       ProviderConfig(enabled=False),
    "zai":         ProviderConfig(enabled=False),
}


# ---------------------------------------------------------------------------
# Root Settings model
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """
    Full CLASP configuration.  Sources (highest → lowest priority):

    1. init kwargs (tests / programmatic overrides)
    2. Environment variables  (CLASP_PORT, CLASP_API_KEY, CLASP_LOG_LEVEL, …)
    3. ~/.clasp/config.yaml
    4. Defaults coded into each field
    """

    model_config = SettingsConfigDict(
        # Don't crash on extra YAML keys we don't yet model.
        extra="ignore",
        # Let sub-models pick up CLASP__SERVER__PORT style env vars too.
        env_nested_delimiter="__",
    )

    # ── Top-level env-var shortcuts ────────────────────────────────────────
    # These map directly to server sub-fields for convenience.
    # CLASP_PORT / CLASP_API_KEY / CLASP_LOG_LEVEL are handled by the
    # validator below so that they overlay the already-parsed server block.

    server: ServerConfig = Field(default_factory=ServerConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    provider_chain: list[str] = Field(default_factory=lambda: list(_DEFAULT_PROVIDER_CHAIN))
    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {k: v.model_copy() for k, v in _DEFAULT_PROVIDERS.items()}
    )
    cache: CacheConfig = Field(default_factory=CacheConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    shared_pool: SharedPoolConfig = Field(default_factory=SharedPoolConfig)

    enable_web_server_tools: bool = Field(default=False, validation_alias="ENABLE_WEB_SERVER_TOOLS")
    web_fetch_allowed_schemes: str = Field(default="http,https", validation_alias="WEB_FETCH_ALLOWED_SCHEMES")
    web_fetch_allow_private_networks: bool = Field(default=False, validation_alias="WEB_FETCH_ALLOW_PRIVATE_NETWORKS")

    # Agent Optimizations & Headless Bots
    fast_prefix_detection: bool = Field(default=True, validation_alias="FAST_PREFIX_DETECTION")
    enable_network_probe_mock: bool = Field(default=True, validation_alias="ENABLE_NETWORK_PROBE_MOCK")
    enable_title_generation_skip: bool = Field(default=True, validation_alias="ENABLE_TITLE_GENERATION_SKIP")
    enable_suggestion_mode_skip: bool = Field(default=True, validation_alias="ENABLE_SUGGESTION_MODE_SKIP")
    enable_filepath_extraction_mock: bool = Field(default=True, validation_alias="ENABLE_FILEPATH_EXTRACTION_MOCK")

    # Messaging logs and diagnostics
    log_raw_messaging_content: bool = Field(default=False, validation_alias="LOG_RAW_MESSAGING_CONTENT")
    log_raw_cli_diagnostics: bool = Field(default=False, validation_alias="LOG_RAW_CLI_DIAGNOSTICS")
    log_messaging_error_details: bool = Field(default=False, validation_alias="LOG_MESSAGING_ERROR_DETAILS")
    debug_platform_edits: bool = Field(default=False, validation_alias="DEBUG_PLATFORM_EDITS")
    debug_subagent_stack: bool = Field(default=False, validation_alias="DEBUG_SUBAGENT_STACK")


    # ── Env-var overrides for server sub-fields ────────────────────────────

    @model_validator(mode="after")
    def _apply_env_overrides(self) -> "Settings":
        """
        Fold CLASP_PORT / CLASP_API_KEY / CLASP_LOG_LEVEL into the already-
        parsed server block, and inject provider API keys from env vars.
        """
        env = os.environ

        # Server shortcuts
        server_updates = {}
        if port_str := env.get("CLASP_PORT"):
            try:
                server_updates["port"] = int(port_str)
            except ValueError:
                logger.warning("CLASP_PORT is not a valid integer", value=port_str)

        if api_key := env.get("CLASP_API_KEY"):
            server_updates["api_key"] = api_key

        if log_level := env.get("CLASP_LOG_LEVEL"):
            server_updates["log_level"] = log_level.upper()

        if server_updates:
            self.server = self.server.model_copy(update=server_updates)

        # Provider key injection from environment
        _KEY_ENV_VARS: dict[str, str] = {
            "nvidia_nim":  "NVIDIA_NIM_API_KEY",
            "gemini":      "GEMINI_API_KEY",
            "cerebras":    "CEREBRAS_API_KEY",
            "groq":        "GROQ_API_KEY",
            "fireworks":   "FIREWORKS_API_KEY",
            "openrouter":  "OPENROUTER_API_KEY",
            "mistral":     "MISTRAL_API_KEY",
            "together":    "TOGETHER_API_KEY",
            "mistral_codestral": "CODESTRAL_API_KEY",
            "deepseek":    "DEEPSEEK_API_KEY",
            "kimi":        "KIMI_API_KEY",
            "opencode":    "OPENCODE_API_KEY",
            "opencode_go": "OPENCODE_API_KEY",
            "wafer":       "WAFER_API_KEY",
            "zai":         "ZAI_API_KEY",
        }

        for provider_name, env_var in _KEY_ENV_VARS.items():
            raw = env.get(env_var, "").strip()
            if not raw:
                continue
            # Support comma-separated multi-key: NVIDIA_NIM_API_KEY=key1,key2,key3
            injected_keys = [k.strip() for k in raw.split(",") if k.strip()]
            if not injected_keys:
                continue
            pcfg = self.providers.setdefault(
                provider_name, ProviderConfig(enabled=True)
            )
            # Merge: deduplicate while preserving order
            existing = list(pcfg.keys)
            for k in injected_keys:
                if k not in existing:
                    existing.append(k)
            self.providers[provider_name] = pcfg.model_copy(update={"keys": existing, "enabled": True})

        return self

    # ── Convenience helpers ────────────────────────────────────────────────

    def enabled_providers(self) -> list[str]:
        """Return provider names that are both in provider_chain and enabled."""
        return [
            name
            for name in self.provider_chain
            if self.providers.get(name, ProviderConfig()).enabled
        ]

    def web_fetch_allowed_scheme_set(self) -> frozenset[str]:
        return frozenset(s.strip().lower() for s in self.web_fetch_allowed_schemes.split(",") if s.strip())

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
        **kwargs: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: init > env > YAML > defaults (secrets omitted)
        return (
            init_settings,
            env_settings,
            _YamlConfigSource(settings_cls),
        )


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the process-wide Settings singleton.

    The result is cached after the first call.  Call
    ``get_settings.cache_clear()`` before re-calling to force a reload
    (done automatically by ``clasp.config.watcher`` on file changes).
    """
    cfg_path = _get_config_path()
    settings = Settings()
    logger.debug(
        "Settings loaded",
        config_path=str(cfg_path),
        exists=cfg_path.exists(),
        port=settings.server.port,
        providers_enabled=settings.enabled_providers(),
    )
    return settings


def reload_settings() -> Settings:
    """Clear the cached config and load it fresh from disk."""
    get_settings.cache_clear()
    return get_settings()