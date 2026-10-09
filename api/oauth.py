"""Provider credential helpers shared by chat streaming and the model routes.

OAuth logins themselves are Setup, done by the Operator with Hermes Agent's own
tools; GFIT-CoWork only reads the resulting ``auth.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Compatibility for older helper tests and self-heal code that import these.
AUTH_JSON_PATH = Path.home() / ".hermes" / "auth.json"

def resolve_runtime_provider_with_anthropic_env_lock(resolver, *args, **kwargs):
    """Resolve runtime credentials under the process-env lock.

    Request paths must resolve Anthropic env fallbacks per outbound request,
    not cache ANTHROPIC_TOKEN or ANTHROPIC_API_KEY. Sharing the process-env
    lock prevents a chat stream from observing one stale Anthropic env value
    while another request is changing the other.
    """
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        return resolver(*args, **kwargs)


# ── legacy auth.json helpers ────────────────────────────────────────────────

def _read_auth_json(auth_path: Path | None = None) -> dict[str, Any]:
    """Read auth.json and return parsed dict, or an empty compatible store."""
    path = auth_path or AUTH_JSON_PATH
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return {}
    return {}


def read_auth_json():
    """Public wrapper for streaming credential self-heal code."""
    return _read_auth_json()

