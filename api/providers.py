"""GFIT-CoWork -- provider credential helpers.

Reads a Profile's provider keys and credential pool for the model picker and
chat. Provider Setup is the Operator's, with `hermes setup` / `hermes model`;
the web app has no provider settings page (remove-admin ticket 04).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:  # POSIX-only; Windows-style environments fall back to process-local locking.
    import fcntl
except ImportError:  # pragma: no cover - exercised only where fcntl is unavailable
    fcntl = None  # type: ignore[assignment]

from api.config import (
    _custom_provider_slug_from_name,
    _pool_entry_payloads,
    _thread_local_env_value,
    get_config,
)
from api.plugin_providers import (
    effective_provider_env_var,
)

logger = logging.getLogger(__name__)


def _provider_env_var_for(provider_id: str) -> str | None:
    """Resolve the API-key env var for a provider (static table + plugin profiles)."""
    return effective_provider_env_var(provider_id, _PROVIDER_ENV_VAR)


def _custom_provider_name_matches(provider_id: str, name: object) -> bool:
    """Return True when *provider_id* refers to a named custom provider."""
    pid = str(provider_id or "").strip().lower()
    raw_name = str(name or "").strip().lower()
    if not pid or not raw_name:
        return False
    slug = _custom_provider_slug_from_name(raw_name)
    candidates = {raw_name, f"custom:{raw_name}"}
    if slug:
        candidates.add(slug)
    return pid in candidates


# SECTION: Provider ↔ env var mapping

# Maps canonical provider slug → env var name for API key.
# Providers not listed here (OAuth/token-flow providers like copilot, nous,
# openai-codex) cannot have their keys managed from the WebUI.
_PROVIDER_ENV_VAR: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "zai": "GLM_API_KEY",
    "kimi-coding": "KIMI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "x-ai": "XAI_API_KEY",
    "xiaomi": "XIAOMI_API_KEY",
    "neuralwatt": "NEURALWATT_API_KEY",
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "opencode-go": "OPENCODE_GO_API_KEY",
    # NOTE: bare "ollama" (local) deliberately omitted — local Ollama is keyless
    # by default and the runtime in hermes_cli/runtime_provider.py only consumes
    # OLLAMA_API_KEY when the base URL hostname is ollama.com (Ollama Cloud).
    # If we mapped both providers to the same env var, configuring Ollama Cloud
    # would falsely flip the local Ollama card to "API key configured" (#1410).
    # Users who genuinely run an authenticated local Ollama can still set a key
    # via providers.ollama.api_key in config.yaml — that path remains supported
    # by _provider_has_key().
    "ollama-cloud": "OLLAMA_API_KEY",
    # Bare "lmstudio" maps to LM_API_KEY — the canonical env var the agent CLI
    # runtime reads (hermes_cli/auth.py:182, api_key_env_vars=("LM_API_KEY",)).
    # Pre-#1499/#1500 the WebUI used LMSTUDIO_API_KEY here, which made Settings
    # report keys correctly but the agent runtime ignored them — masked in
    # practice by the LMSTUDIO_NOAUTH_PLACEHOLDER for keyless local installs.
    # Aligning to LM_API_KEY makes a configured LM Studio key actually work
    # for chat. The legacy LMSTUDIO_API_KEY name is read by `_provider_has_key`
    # via _PROVIDER_ENV_VAR_ALIASES below so existing users don't see Settings
    # flip to "no key" after upgrading.
    "lmstudio": "LM_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}

# Read-only legacy env-var aliases.  When `_provider_has_key(pid)` looks up its
# canonical env var name and finds nothing, it also checks any aliases listed
# here.  Hermes Agent's Setup writes only the canonical name.  Use this for env vars that were renamed in a past release;
# add an entry, ship for a few releases, then remove the alias once enough
# users have upgraded.
_PROVIDER_ENV_VAR_ALIASES: dict[str, tuple[str, ...]] = {
    # #1500 — agent runtime reads LM_API_KEY (canonical), but WebUI builds
    # ≤ v0.50.272 wrote LMSTUDIO_API_KEY into .env.  Keep reading both.
    "lmstudio": ("LMSTUDIO_API_KEY",),
    # #3145 — provider detection treats OPENCODE_API_KEY as enabling both
    # OpenCode Zen and OpenCode Go. The runtime-facing lookup must read the same
    # shared bridge key after the provider-specific slot, otherwise Settings can
    # show the groups as configured while chat fails the no-key path.
    "opencode-zen": ("OPENCODE_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
}

def _provider_credential_env_vars() -> tuple[str, ...]:
    names = {name for name in _PROVIDER_ENV_VAR.values() if name}
    for aliases in _PROVIDER_ENV_VAR_ALIASES.values():
        for alias in aliases or ():
            if alias:
                names.add(alias)
    return tuple(sorted(names))


def _entry_value(entry, *names):
    for name in names:
        try:
            value = getattr(entry, name)
        except Exception:
            value = None
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _entry_exhausted_ttl_seconds(error_code):
    code = str(error_code or "").strip()
    if code == "401":
        return 5 * 60
    if code == "402":
        # #6626: keep WebUI's eligibility decision tied to the installed
        # runtime contract. The runtime routes 402 via
        # credential_pool._exhausted_ttl() (120s when the new
        # EXHAUSTED_TTL_402_SECONDS is present, 1h fallback otherwise).
        # Hard-coding 120s here would let display/probe code mark an entry
        # usable before CredentialPool.select() is willing to lease it on
        # mixed-version installations.
        try:
            from agent.credential_pool import _exhausted_ttl as _runtime_exhausted_ttl
            return _runtime_exhausted_ttl(int(code))
        except Exception:
            return 60 * 60
    return 60 * 60


def _entry_pool_exhausted_until(entry):
    if str(_entry_value(entry, "last_status") or "").strip().lower() != "exhausted":
        return None
    reset_at = _parse_dt(getattr(entry, "last_error_reset_at", None))
    if reset_at is not None:
        return reset_at
    status_at = _parse_dt(getattr(entry, "last_status_at", None))
    if status_at is None:
        return None
    return status_at + timedelta(seconds=_entry_exhausted_ttl_seconds(_entry_value(entry, "last_error_code")))


def _entry_is_pool_exhausted(entry):
    exhausted_until = _entry_pool_exhausted_until(entry)
    return exhausted_until is not None and datetime.now(timezone.utc) < exhausted_until


def _get_hermes_home() -> Path:
    """Return the active Hermes home directory."""
    try:
        from api.profiles import get_active_hermes_home
        return get_active_hermes_home()
    except ImportError:
        return Path.home() / ".hermes"


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Read key=value pairs from a .env file."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return values


def _decode_jwt_claims_unverified(token: str) -> dict[str, Any]:
    """Decode JWT claims for token-shape classification only.

    The signature is intentionally not verified because this helper is not an
    authorization decision: it only prevents a Codex OAuth JWT-shaped value from
    being treated as a raw OpenAI API key in provider-card detection.
    """
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".", 2)[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _looks_like_codex_oauth_token(value: str) -> bool:
    """Return True when a value is a ChatGPT/Codex OAuth JWT, not an OpenAI API key."""
    token = str(value or "").strip()
    if not token or token.startswith("sk-"):
        return False
    claims = _decode_jwt_claims_unverified(token)
    if not claims:
        return False
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict) and auth_claim:
        return True
    return any(key in claims for key in ("chatgpt_account_id", "https://api.openai.com/profile"))


def _provider_value_counts_as_api_key(provider_id: str, value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if (provider_id or "").strip().lower() == "openai" and _looks_like_codex_oauth_token(text):
        return False
    return True


def _write_env_file(env_path: Path, updates: dict[str, str | None], *, set_process_env: bool = True) -> None:
    """Write key=value pairs to the .env file.

    Values of ``None`` cause the key to be removed.

    With *set_process_env* false, ``os.environ`` is left alone: for writing a
    Profile's ``.env`` that is not the active one (another Profile's value must
    never become this process's).

    Preserves comments, blank lines, and original key order (#1164).
    New keys are appended at the end of the file with a blank-line separator.

    Holds ``_ENV_LOCK`` from ``api.streaming`` for the entire load → modify →
    write cycle to prevent TOCTOU races between concurrent POST /api/providers
    calls (each reading the same file baseline and overwriting the other's key).
    Also serialises os.environ mutations with streaming sessions.
    """
    from api.streaming import _ENV_LOCK
    import stat as _stat

    with _ENV_LOCK:
        # ── Read existing lines (preserving comments and blank lines) ──
        existing_lines: list[str] = []
        if env_path.exists():
            try:
                existing_lines = env_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                existing_lines = []

        # Map each existing key to its line index so we can update in-place.
        existing_key_indices: dict[str, int] = {}
        for _i, _raw in enumerate(existing_lines):
            _stripped = _raw.strip()
            if _stripped and not _stripped.startswith("#") and "=" in _stripped:
                _existing_key_indices_key = _stripped.split("=", 1)[0].strip()
                existing_key_indices[_existing_key_indices_key] = _i

        output_lines = list(existing_lines)
        new_keys: list[str] = []

        for key, value in updates.items():
            if value is None:
                # Mark the line for removal (None sentinel) and clear env.
                if set_process_env:
                    os.environ.pop(key, None)
                if key in existing_key_indices:
                    output_lines[existing_key_indices[key]] = None  # type: ignore[assignment]
                continue
            clean = str(value).strip()
            if not clean:
                continue
            # Reject embedded newlines/carriage returns to prevent .env injection
            if "\n" in clean or "\r" in clean:
                raise ValueError("API key must not contain newline characters.")
            if set_process_env:
                os.environ[key] = clean

            if key in existing_key_indices:
                output_lines[existing_key_indices[key]] = f"{key}={clean}"
            else:
                new_keys.append(f"{key}={clean}")

        # Remove deleted lines (None sentinels)
        output_lines = [l for l in output_lines if l is not None]

        # Append new keys after a blank-line separator
        if new_keys:
            if output_lines and output_lines[-1].strip() != "":
                output_lines.append("")
            output_lines.extend(new_keys)

        env_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(output_lines)
        if content:
            content += "\n"
        # Atomic write via tempfile + os.replace so cross-process readers
        # (Telegram bot, CLI) never see a half-truncated file.  The shared
        # ``~/.hermes/.env`` is also written by ``hermes_cli.config.save_env_value``
        # using the same atomic pattern; matching it here closes the
        # cross-process leg of #1164 (within-process is covered by _ENV_LOCK).
        _mode = _stat.S_IRUSR | _stat.S_IWUSR  # 0o600
        import tempfile as _tempfile
        _tmp_fd, _tmp_path = _tempfile.mkstemp(
            dir=str(env_path.parent), prefix=".env_", suffix=".tmp"
        )
        try:
            with os.fdopen(_tmp_fd, "w", encoding="utf-8") as _f:
                _f.write(content)
                _f.flush()
                os.fsync(_f.fileno())
            os.chmod(_tmp_path, _mode)  # tighten before rename so readers see 0600
            os.replace(_tmp_path, env_path)
        except BaseException:
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass
            raise
        try:
            env_path.chmod(_mode)
        except OSError:
            pass


def _provider_has_key(provider_id: str) -> bool:
    """Check whether a provider has a configured API key.

    Checks (in order):
    1. ``~/.hermes/.env`` for the known env var
    2. ``os.environ`` for the known env var
    3. ``config.yaml → model.api_key`` (only if provider is the active one)
    4. ``config.yaml → providers.<id>.api_key``
    5. ``config.yaml → custom_providers[].api_key`` (for custom providers)
    """
    env_var = _provider_env_var_for(provider_id)
    if env_var:
        env_path = _get_hermes_home() / ".env"
        env_values = _load_env_file(env_path)
        env_file_value = env_values.get(env_var)
        if _provider_value_counts_as_api_key(provider_id, env_file_value):
            return True
        env_value = _thread_local_env_value(env_var)
        if _provider_value_counts_as_api_key(provider_id, env_value):
            return True
        # Fall back to legacy env-var aliases (e.g. lmstudio's pre-#1500
        # LMSTUDIO_API_KEY name) so existing users don't lose detection
        # after an env-var rename.  See _PROVIDER_ENV_VAR_ALIASES.
        for alias in _PROVIDER_ENV_VAR_ALIASES.get(provider_id, ()) or ():
            if _provider_value_counts_as_api_key(provider_id, env_values.get(alias)):
                return True
            if _provider_value_counts_as_api_key(provider_id, _thread_local_env_value(alias)):
                return True
    # Check credential pool — covers custom providers registered via
    # `hermes auth add` which store keys in auth.json (not config.yaml).
    # Must be outside the `if env_var:` block above: custom providers
    # (custom:bothub, etc.) have no env var, so that block is skipped.
    # _has_explicit_pool_credentials reads the Profile's own auth.json (never
    # load_pool, which seeds and persists) and filters gh-cli / GITHUB_TOKEN
    # ambient entries so copilot doesn't appear just because `gh` is installed.
    try:
        from api.config import _has_explicit_pool_credentials
        if _has_explicit_pool_credentials(provider_id):
            return True
    except ImportError:
        pass

    cfg = get_config()
    # Check model.api_key — only match if this provider is the active one.
    # Previously this checked globally, causing all providers to show
    # "configured" when the active provider had a top-level api_key.
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict) and str(model_cfg.get("api_key") or "").strip():
        active_provider = model_cfg.get("provider")
        if active_provider and str(active_provider).strip().lower() == provider_id.lower():
            if _provider_value_counts_as_api_key(provider_id, model_cfg.get("api_key")):
                return True
    # Check providers.<id>.api_key
    providers_cfg = cfg.get("providers") or {}
    if isinstance(providers_cfg, dict):
        provider_cfg = providers_cfg.get(provider_id, {})
        if isinstance(provider_cfg, dict) and str(provider_cfg.get("api_key") or "").strip():
            if _provider_value_counts_as_api_key(provider_id, provider_cfg.get("api_key")):
                return True
    # Check custom_providers
    custom_providers = cfg.get("custom_providers", [])
    if isinstance(custom_providers, list):
        for cp in custom_providers:
            if isinstance(cp, dict):
                if _custom_provider_name_matches(provider_id, cp.get("name")):
                    if _provider_value_counts_as_api_key(provider_id, cp.get("api_key")):
                        return True
    return False


def _get_provider_api_key(provider_id: str) -> str | None:
    """Return a configured provider API key without exposing it to callers."""
    provider_id = (provider_id or "").strip().lower()
    env_var = _provider_env_var_for(provider_id)
    if env_var:
        env_path = _get_hermes_home() / ".env"
        env_values = _load_env_file(env_path)
        env_file_value = env_values.get(env_var)
        if _provider_value_counts_as_api_key(provider_id, env_file_value):
            return str(env_file_value).strip() or None
        env_value = _thread_local_env_value(env_var)
        if _provider_value_counts_as_api_key(provider_id, env_value):
            return str(env_value).strip() or None
        for alias in _PROVIDER_ENV_VAR_ALIASES.get(provider_id, ()) or ():
            alias_file_value = env_values.get(alias)
            if _provider_value_counts_as_api_key(provider_id, alias_file_value):
                return str(alias_file_value).strip() or None
            alias_value = _thread_local_env_value(alias)
            if _provider_value_counts_as_api_key(provider_id, alias_value):
                return str(alias_value).strip() or None

    cfg = get_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        active_provider = str(model_cfg.get("provider") or "").strip().lower()
        model_key = str(model_cfg.get("api_key") or "").strip()
        if model_key and active_provider == provider_id and _provider_value_counts_as_api_key(provider_id, model_key):
            return model_key

    providers_cfg = cfg.get("providers") or {}
    if isinstance(providers_cfg, dict):
        provider_cfg = providers_cfg.get(provider_id, {})
        if isinstance(provider_cfg, dict):
            provider_key = str(provider_cfg.get("api_key") or "").strip()
            if _provider_value_counts_as_api_key(provider_id, provider_key):
                return provider_key

    custom_providers = cfg.get("custom_providers", [])
    if isinstance(custom_providers, list):
        for cp in custom_providers:
            if not isinstance(cp, dict):
                continue
            if _custom_provider_name_matches(provider_id, cp.get("name")):
                cp_key = str(cp.get("api_key") or "").strip()
                if cp_key.startswith("${") and cp_key.endswith("}"):
                    return _thread_local_env_value(cp_key[2:-1]).strip() or None
                if _provider_value_counts_as_api_key(provider_id, cp_key):
                    return cp_key
    # Fallback: try credential pool (e.g. bothub key stored via auth.json)
    for entry in _pool_entry_payloads(provider_id):
        status = str(entry.get("last_status") or "").strip().lower()
        if status == "dead":
            continue
        if status == "exhausted":
            ns = SimpleNamespace(**entry)
            if _entry_is_pool_exhausted(ns):
                continue
        key = str(
            entry.get("runtime_api_key")
            or entry.get("agent_key")
            or entry.get("access_token")
            or ""
        ).strip()
        if key:
            return key
    return None


def provider_has_usable_pool_credential(provider_id: str, *, refresh: bool = False) -> bool:
    """Return True only when the provider's credential-pool lane has a usable entry."""
    provider = str(provider_id or "").strip().lower()
    if not provider:
        return False
    if refresh:
        try:
            from api.config import invalidate_credential_pool_cache

            invalidate_credential_pool_cache(provider)
        except Exception:
            logger.debug("Failed to refresh credential pool before pool availability check", exc_info=True)
    for entry in _pool_entry_payloads(provider):
        status = str(entry.get("last_status") or "").strip().lower()
        if status == "dead":
            continue
        if status == "exhausted":
            ns = SimpleNamespace(**entry)
            if _entry_is_pool_exhausted(ns):
                continue
        key = str(
            entry.get("runtime_api_key")
            or entry.get("agent_key")
            or entry.get("access_token")
            or ""
        ).strip()
        if key:
            return True
    return False


def _credential_secret_fingerprint(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _entry_secret_fingerprint(entry: dict) -> str:
    value = str(entry.get("secret_fingerprint") or "").strip().lower()
    if value.startswith("sha256:"):
        value = value[len("sha256:"):]
    if not value:
        return ""
    if all(ch in "0123456789abcdef" for ch in value):
        return value[:16]
    return ""


def _pool_entry_currently_unusable(entry: dict) -> bool:
    status = str(entry.get("last_status") or "").strip().lower()
    if status == "dead":
        return True
    if status == "exhausted":
        ns = SimpleNamespace(**entry)
        return _entry_is_pool_exhausted(ns)
    return False


def provider_has_process_wakeup_recovery_credential(provider_id: str, *, refresh: bool = False) -> bool:
    """Return True when a paused credential-pool wakeup lane can safely retry."""
    provider = str(provider_id or "").strip().lower()
    if not provider:
        return False
    if provider_has_usable_pool_credential(provider, refresh=refresh):
        return True
    configured_key = _get_provider_api_key(provider)
    if not configured_key:
        return False
    configured_fingerprint = _credential_secret_fingerprint(configured_key)
    if not configured_fingerprint:
        return False
    has_unusable_pool_entry = False
    has_unknown_unusable_pool_entry = False
    for entry in _pool_entry_payloads(provider):
        entry_fingerprint = _entry_secret_fingerprint(entry)
        if not _pool_entry_currently_unusable(entry):
            if entry_fingerprint and entry_fingerprint == configured_fingerprint:
                return True
            continue
        has_unusable_pool_entry = True
        if not entry_fingerprint:
            has_unknown_unusable_pool_entry = True
            continue
        if entry_fingerprint == configured_fingerprint:
            return False
    return has_unusable_pool_entry and not has_unknown_unusable_pool_entry
