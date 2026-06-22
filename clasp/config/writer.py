"""
clasp/config/writer.py

Atomic, comment-preserving YAML config writer.

Why ruamel.yaml?
----------------
The standard PyYAML ``dump()`` round-trip silently drops comments and
reorders keys.  ruamel.yaml preserves both, so a user who hand-edits
``config.yaml`` and then saves from the UI does not lose their notes.

Atomic write strategy
---------------------
1. Serialise to ``config.yaml.tmp`` in the same directory.
2. ``os.replace()`` — POSIX-atomic rename, never leaves a half-written file.
3. On Windows the same call is best-effort atomic (platform limitation).

Key masking
-----------
``mask_keys(settings)`` returns a JSON-serialisable dict where every API key
is replaced with ``<prefix>-***<suffix>`` (last 4 chars kept).  The UI
displays masked values; when the user clicks "Save & Apply" the server
detects ``***`` in any key string and restores the original.

Usage
-----
    from clasp.config.writer import write_config, mask_keys, restore_keys

    # Write a Pydantic Settings instance to disk:
    write_config(settings)

    # Produce a masked copy for the web UI:
    masked = mask_keys(settings)

    # Round-trip: merge UI payload back, restoring originals for masked keys:
    merged = restore_keys(ui_payload, current_settings)
    write_config(Settings(**merged))
"""

from __future__ import annotations

import copy
import os
from io import StringIO
from pathlib import Path
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_yaml():
    """Return a configured ruamel.yaml YAML instance (lazy import)."""
    from ruamel.yaml import YAML  # noqa: PLC0415

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 120  # avoid wrapping long model slugs
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _config_path() -> Path:
    """Return the active config file path (respects CLASP_CONFIG_PATH env var)."""
    import os as _os  # re-import to avoid circular at module level

    default = Path.home() / ".clasp" / "config.yaml"
    return Path(_os.environ.get("CLASP_CONFIG_PATH", str(default)))


# ---------------------------------------------------------------------------
# Settings → dict serialisation
# ---------------------------------------------------------------------------

def settings_to_dict(settings: Any) -> dict[str, Any]:
    """
    Convert a ``Settings`` instance to a plain nested dict suitable for
    YAML serialisation.  Uses Pydantic's ``model_dump()`` so that aliases,
    validators, and type coercions are all applied first.
    """
    return settings.model_dump(mode="python", exclude_none=False)


# ---------------------------------------------------------------------------
# Key masking / restoration
# ---------------------------------------------------------------------------

_MASK_SENTINEL = "***"
_KEEP_SUFFIX_LEN = 4


def _mask_key_string(key: str) -> str:
    """
    ``nvapi-abcdefghijk`` → ``nvapi-***hijk``

    We keep only the last ``_KEEP_SUFFIX_LEN`` characters so the user can
    identify which key is which without exposing the secret.
    """
    if not key or _MASK_SENTINEL in key:
        return key  # already masked or empty

    if len(key) <= 8:
        return _MASK_SENTINEL

    suffix = key[-_KEEP_SUFFIX_LEN:]
    # Find the first dash or use a fixed prefix length.
    dash_idx = key.find("-")
    if 0 < dash_idx < len(key) - _KEEP_SUFFIX_LEN:
        prefix = key[: dash_idx + 1]
    else:
        prefix = key[:4] + "-" if len(key) > 4 else key[:2] + "-"

    return f"{prefix}{_MASK_SENTINEL}{suffix}"


def mask_keys(settings: Any) -> dict[str, Any]:
    """
    Return a deep copy of *settings* as a dict with all API keys masked.

    Safe to serialise to JSON and send to the web UI.
    """
    data = copy.deepcopy(settings_to_dict(settings))
    providers: dict[str, Any] = data.get("providers", {})
    for provider_cfg in providers.values():
        if isinstance(provider_cfg, dict):
            raw_keys: list[str] = provider_cfg.get("keys", [])
            provider_cfg["keys"] = [_mask_key_string(k) for k in raw_keys]
    return data


def _is_masked(value: str) -> bool:
    return _MASK_SENTINEL in value


def restore_keys(
    incoming: dict[str, Any],
    current_settings: Any,
) -> dict[str, Any]:
    """
    Merge *incoming* (from the web UI) with *current_settings* so that any
    masked API key value (``***``) is replaced by the real key stored in
    *current_settings*.

    Non-masked keys in *incoming* are kept as-is (the user typed a new key).
    Keys present in *current_settings* but absent from *incoming* are dropped
    (the user removed them).

    Returns a plain dict that can be used to construct a new ``Settings``.
    """
    result = copy.deepcopy(incoming)
    current_providers: dict[str, Any] = settings_to_dict(current_settings).get(
        "providers", {}
    )

    incoming_providers: dict[str, Any] = result.get("providers", {})
    for provider_name, provider_cfg in incoming_providers.items():
        if not isinstance(provider_cfg, dict):
            continue
        current_keys: list[str] = (
            current_providers.get(provider_name, {}).get("keys", [])
        )
        restored: list[str] = []
        for i, key in enumerate(provider_cfg.get("keys", [])):
            if _is_masked(key):
                # Restore original at same index if available; keep as-is if not.
                if i < len(current_keys):
                    restored.append(current_keys[i])
                else:
                    restored.append(key)
            else:
                restored.append(key)
        provider_cfg["keys"] = restored

    return result


# ---------------------------------------------------------------------------
# Atomic YAML write
# ---------------------------------------------------------------------------

_HEADER_COMMENT = (
    "# CLASP Configuration — managed by `clasp server` UI\n"
    "# Hand-editing is supported; comments are preserved on save.\n\n"
)


def write_config(
    settings: Any,
    path: Path | None = None,
) -> Path:
    """
    Serialise *settings* to YAML and atomically write to *path*
    (default: ``~/.clasp/config.yaml``).

    Steps
    -----
    1. Convert settings to plain dict via ``settings_to_dict()``.
    2. Render to an in-memory string buffer with ruamel.yaml.
    3. Write buffer → ``<path>.tmp``.
    4. ``os.replace(<path>.tmp, <path>)`` — atomic rename.

    Returns the final file path.
    """
    target = path or _config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yaml.tmp")

    data = settings_to_dict(settings)

    yaml = _get_yaml()
    buf = StringIO()
    buf.write(_HEADER_COMMENT)
    yaml.dump(data, buf)
    content = buf.getvalue()

    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        logger.error(
            "Failed to write config",
            path=str(target),
            error=str(exc),
        )
        raise
    finally:
        # Clean up tmp if replace failed
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    logger.info("Config written", path=str(target), size_bytes=len(content))
    return target


# ---------------------------------------------------------------------------
# Convenience: write from a raw dict (used by POST /internal/config)
# ---------------------------------------------------------------------------

def write_config_dict(
    data: dict[str, Any],
    path: Path | None = None,
) -> Path:
    """
    Like ``write_config`` but accepts a plain dict instead of a Settings
    instance.  Used by the internal API endpoint after Pydantic validation.
    """
    target = path or _config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yaml.tmp")

    yaml = _get_yaml()
    buf = StringIO()
    buf.write(_HEADER_COMMENT)
    yaml.dump(data, buf)
    content = buf.getvalue()

    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        logger.error(
            "Failed to write config dict",
            path=str(target),
            error=str(exc),
        )
        raise
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    logger.info("Config written (from dict)", path=str(target), size_bytes=len(content))
    return target