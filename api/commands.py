"""Expose hermes-agent's COMMAND_REGISTRY to the webui frontend.

This module is the single integration point with hermes_cli.commands.
If hermes-agent is unavailable the endpoint degrades to an empty list
so the frontend can still load with WEBUI_ONLY commands.
"""
from __future__ import annotations
from contextlib import nullcontext
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Commands that are gateway_only in the agent registry -- webui never
# wants to expose them (sethome, restart, update etc.) even if a future
# agent version drops the gateway_only flag. /commands is the agent's
# own command-listing command; webui has its own /help that calls
# cmdHelp() locally, so /commands would be redundant and confusing.
_NEVER_EXPOSE: frozenset[str] = frozenset({
    'sethome', 'restart', 'update', 'commands',
})


# Narrow agent-side execution allowlist for /api/commands/exec.
_AGENT_COMMAND_ALIASES = {
    'reload_mcp': 'reload-mcp',
    'reload_skills': 'reload-skills',
    'codex_runtime': 'codex-runtime',
}
def _parse_agent_command(command: str) -> tuple[str, str]:
    """Return ``(canonical_name, arg_string)`` from slash-command text."""

    cmd_base, arg_string = _parse_slash_command(command)
    return _AGENT_COMMAND_ALIASES.get(cmd_base, cmd_base), arg_string


def _parse_slash_command(command: str) -> tuple[str, str]:
    """Return ``(command_name, arg_string)`` from slash-command text."""

    raw = str(command or "").strip()
    if not raw:
        raise ValueError("command is required")

    cmd_text = raw[1:] if raw.startswith("/") else raw
    cmd_parts = cmd_text.split(maxsplit=1)
    cmd_base = (cmd_parts[0] if cmd_parts else "").strip().lower()
    if not cmd_base:
        raise ValueError("command is required")

    return cmd_base, cmd_parts[1] if len(cmd_parts) > 1 else ""


def _bundle_profile_context(purpose: str):
    """Resolve the active-profile env wrapper used by bundle APIs."""

    try:
        from api.profiles import profile_env_for_active_request
    except ImportError:
        return nullcontext()
    return profile_env_for_active_request(purpose, logger_override=logger)


def _normalize_agent_command_name(command: str) -> str:
    """Normalize slash text to a canonical command name."""

    canonical, _arg_string = _parse_agent_command(command)
    return canonical


def list_commands(_registry=None) -> list[dict[str, Any]]:
    """Return COMMAND_REGISTRY entries as JSON-friendly dicts.

    Returns empty list if hermes_cli is not installed (graceful
    degradation -- the frontend has its own fallback minimum set).

    Args:
        _registry: Optional injected registry for testing. When None
            (production), imports COMMAND_REGISTRY from hermes_cli.
    """
    if _registry is None:
        try:
            from hermes_cli.commands import COMMAND_REGISTRY as _registry
        except ImportError:
            logger.warning("hermes_cli.commands not importable -- /api/commands returns []")
            return []

    out: list[dict[str, Any]] = []
    for cmd in _registry:
        if cmd.gateway_only:
            continue
        if cmd.name in _NEVER_EXPOSE:
            continue
        out.append({
            'name': cmd.name,
            'description': cmd.description,
            'category': cmd.category,
            'aliases': list(cmd.aliases),
            'args_hint': cmd.args_hint,
            'subcommands': list(cmd.subcommands),
            'cli_only': bool(cmd.cli_only),
            'gateway_only': bool(cmd.gateway_only),
        })

    # Include plugin-registered slash commands
    try:
        from hermes_cli.plugins import get_plugin_commands
        plugin_cmds = get_plugin_commands() or {}
        existing_names = {c['name'] for c in out}
        for cmd_name, cmd_info in plugin_cmds.items():
            if cmd_name in existing_names or cmd_name in _NEVER_EXPOSE:
                continue
            out.append({
                'name': cmd_name,
                'description': str(cmd_info.get('description', 'Plugin command')),
                'category': 'Plugin',
                'aliases': [],
                'args_hint': str(cmd_info.get('args_hint', '')),
                'subcommands': [],
                'cli_only': False,
                'gateway_only': False,
            })
    except Exception:
        pass
    return out


def list_command_bundles() -> list[dict[str, Any]]:
    """Return installed skill bundles for the active WebUI profile."""

    try:
        from agent.skill_bundles import list_bundles as _list_bundles
    except ImportError:
        logger.debug("agent.skill_bundles not importable -- /api/commands/bundles returns []")
        return []

    try:
        with _bundle_profile_context("/api/commands/bundles"):
            bundles = _list_bundles() or []
    except Exception:
        logger.warning("Failed to list skill bundles", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for bundle in bundles:
        slug = str((bundle or {}).get("slug", "")).strip().lower()
        if not slug:
            continue
        skills = list((bundle or {}).get("skills") or [])
        out.append({
            "name": slug,
            "description": str((bundle or {}).get("description") or "").strip() or "Skill bundle",
            "skill_count": len(skills),
            "source": "bundle",
        })
    return out


def resolve_bundle_command(command: str) -> dict[str, Any]:
    """Expand a bundle slash command into the backend invocation payload."""

    bundle_name, user_instruction = _parse_slash_command(command)
    try:
        from agent.skill_bundles import (
            build_bundle_invocation_message,
            resolve_bundle_command_key,
        )
    except ImportError as exc:
        logger.warning("Skill bundle runtime unavailable", exc_info=True)
        raise RuntimeError("Skill bundle runtime unavailable") from exc

    try:
        with _bundle_profile_context("/api/commands/bundles/resolve"):
            bundle_key = resolve_bundle_command_key(bundle_name)
            if bundle_key is None:
                raise KeyError(bundle_name)
            bundle_result = build_bundle_invocation_message(bundle_key, user_instruction)
    except (KeyError, ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.warning("Failed to resolve skill bundle command", exc_info=True)
        raise RuntimeError("Skill bundle command unavailable") from exc

    if not bundle_result:
        raise RuntimeError("Bundle command returned no invocation text")

    message, loaded_skills, missing_skills = bundle_result
    resolved_message = str(message or "").strip()
    if not resolved_message:
        raise RuntimeError("Bundle command returned no invocation text")

    return {
        "name": bundle_key.lstrip("/"),
        "source": "bundle",
        "message": resolved_message,
        "loaded_skills": list(loaded_skills or []),
        "missing_skills": list(missing_skills or []),
    }


def _load_config_for_moa_resolution() -> dict:
    from hermes_cli.config import load_config

    cfg = load_config()
    return cfg if isinstance(cfg, dict) else {}


def resolve_moa_config(preset: str | None = None) -> dict:
    try:
        from hermes_cli.moa_config import moa_usage, normalize_moa_config
    except ImportError as exc:
        raise RuntimeError("MoA runtime unavailable (hermes-agent not installed or too old)") from exc
    try:
        from hermes_cli.moa_config import resolve_moa_preset
    except ImportError:
        resolve_moa_preset = None

    try:
        cfg = _load_config_for_moa_resolution()
        moa_raw = cfg.get("moa") if isinstance(cfg, dict) else {}
        moa_cfg = normalize_moa_config(moa_raw)
    except Exception:
        moa_raw = {}
        moa_cfg = normalize_moa_config({})

    preset_name = str(preset or moa_cfg.get("default_preset") or "default").strip()
    if preset_name not in (moa_cfg.get("presets") or {}):
        preset_name = str(moa_cfg.get("default_preset") or "default")

    selected = {}
    if resolve_moa_preset is not None:
        try:
            selected = resolve_moa_preset(moa_raw, preset_name)
            if not isinstance(selected, dict):
                selected = {}
        except Exception:
            selected = {}
            preset_name = str(moa_cfg.get("default_preset") or "default")

    resolved = dict(moa_cfg)
    resolved.update(selected)
    resolved["preset"] = preset_name
    resolved["usage"] = moa_usage()
    return resolved

