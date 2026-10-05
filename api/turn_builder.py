"""GFIT-CoWork -- the turn builder: what a WebUI Hermes Agent turn is told, and how a WebUI agent is made.

The streaming chat turn and the non-streaming chat route take their prompts
from here; the agents the route module makes (non-streaming chat, manual
compression, the handoff summary, the git commit-message helper) are made
here. The streaming turn still builds its own agent arguments: its self-heal
and provider-retry paths mutate and reuse them.

- :func:`turn_prompts` -- a chat turn's system message (it names the Workspace
  the session was created in, so a Workspace switch never rewrites the first
  message and invalidates the model's prefix cache, #6672), its ephemeral
  prompt (personality, WebUI surface context, progress guidance, delivery
  context) and the ``[Workspace::v1: ...]`` prefix for the live Workspace;
- :func:`personality_prompt` -- a session personality's text from config;
- :func:`webui_agent` -- an agent with the WebUI platform, quiet mode and the
  resolved provider bundle's extra arguments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from api.config import get_config


PROGRESS_PROMPT = """
WebUI progress guidance:
- Match the normal Hermes messaging style, but do not let long tool-running WebUI turns appear silent.
- For long multi-step work that uses tools, emit brief user-visible progress updates as normal assistant content, not only as hidden reasoning.
- Before the first tool batch in a long task, say what you are about to inspect.
- After each meaningful batch of tool calls, say what you just confirmed and what you will check next before continuing with more tools.
- Do not run many independent tool batches back-to-back without visible assistant text between them when the task is still ongoing.
- Do not keep progress only in reasoning, thinking, or tool-result channels; those are not a substitute for visible interim updates.
- Each update should say what you are about to check, what you just confirmed, or why the next tool call is needed.
- Keep updates concise, factual, and in the user's language. One or two short sentences are enough.
- Do not reveal hidden reasoning, chain-of-thought, private scratchpads, secrets, raw logs, or long tool output.
- Password, API-key, token, and secret fields are automatically redacted by the system. Treat masked values as intentional redaction, not placeholder text or user input errors, and do not tell the user a stored credential is wrong based on a masked value alone.
- Final visible assistant replies must be clear, user-facing, and in the user's language, not private planning notes.
- Do not include terse planning fragments or scratchpad shorthand in visible assistant text. Avoid fragments like "Need script", "Need check logs", "Need inspect email", or "maybe invite"; either omit them or rewrite them as clear user-facing progress.
- For direct answers or very short tasks, skip progress updates and answer normally.
""".strip()


def surface_context_prompt(surface_context: Optional[dict]) -> str:
    """Return safe WebUI session metadata for the agent's ephemeral context.

    Messaging gateways inject platform/channel context before each run. Browser
    sessions do not have a chat platform wrapper, so provide an explicit, small
    surface description here instead of relying on the model to infer where it
    is running from the transcript alone.
    """
    if not isinstance(surface_context, dict):
        return ""

    lines = [
        "WebUI session context:",
        "- This browser session is not the same live transcript as Telegram, Discord, Slack, or other messaging surfaces.",
        "- Use durable memory, saved sessions, and available tools for cross-surface recall instead of assuming those transcripts are in this browser chat.",
        "- Do not copy or dump this browser transcript into external notes or durable memory by default.",
        "- Write to external notes or durable memory only for explicit captures, durable user preferences, decisions, blockers/open issues, runbook-worthy workflows, or other clearly reusable signals; otherwise leave notes unchanged.",
        "- When you do write or update a durable note, briefly tell the user what note/section changed so the write is reviewable.",
    ]
    fields = (
        ("source", "Source"),
        ("session_id", "Session ID"),
        ("profile", "Profile"),
        ("workspace", "Workspace"),
    )
    for key, label in fields:
        raw = surface_context.get(key)
        value = str(raw).strip() if raw is not None else ""
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def delivery_context_prompt(config_data: Optional[dict] = None) -> str:
    """Return platform/delivery context for the ephemeral system prompt.

    Connected platforms, home channels, and scheduled-task delivery hints
    are injected into the system prompt (safe for role alternation) rather
    than as a prefill ``user`` message, which strict chat templates (Mistral,
    Gemma) reject.

    NOTE: This function only covers platform/delivery info.  The session
    framing (\"Source: WebUI\", \"Session ID\", \"Profile\", \"Workspace\") is
    emitted by ``surface_context_prompt()``, which is called from
    ``ephemeral_system_prompt()`` before this helper.  If you
    refactor this area, keep that surface call in place — the two helpers
    together produce the full session context block.
    """
    cfg = config_data if isinstance(config_data, dict) else get_config()
    lines: list[str] = []

    display_hermes_home = None
    try:
        from hermes_constants import get_hermes_home, display_hermes_home as _dh
        display_hermes_home = _dh
    except Exception:
        get_hermes_home = None  # type: ignore[assignment]

    connected = ["local (files on this machine)"]
    try:
        if get_hermes_home is not None:
            state_path = get_hermes_home() / "gateway_state.json"
            if state_path.exists():
                raw_state = json.loads(state_path.read_text(encoding="utf-8"))
                platforms = raw_state.get("platforms") if isinstance(raw_state, dict) else {}
                if isinstance(platforms, dict):
                    for name in sorted(platforms):
                        pdata = platforms.get(name) or {}
                        if isinstance(pdata, dict) and pdata.get("state") == "connected" and name != "local":
                            connected.append(f"{name}: Connected ✓")
    except Exception:
        pass
    lines.append(f"**Connected Platforms:** {', '.join(connected)}")

    home_channels = {}
    try:
        platforms_cfg = cfg.get("platforms", {}) if isinstance(cfg, dict) else {}
        if isinstance(platforms_cfg, dict):
            for name, pdata in platforms_cfg.items():
                if not isinstance(pdata, dict):
                    continue
                if pdata.get("enabled") is False:
                    continue
                home = pdata.get("home_channel")
                if isinstance(home, dict):
                    home_channels[str(name)] = str(home.get("name") or name)
    except Exception:
        home_channels = {}

    if home_channels:
        lines.append("")
        lines.append("**Home Channels (default destinations):**")
        for platform, label in sorted(home_channels.items()):
            lines.append(f"  - {platform}: {label}")

    lines.append("")
    lines.append("**Delivery options for scheduled tasks:**")
    lines.append("- `\"origin\"` → Back to this WebUI/browser session when the WebUI runtime supports origin delivery; otherwise prefer an explicit platform target.")
    try:
        home_display = display_hermes_home() if display_hermes_home else "~/.hermes"
    except Exception:
        home_display = "~/.hermes"
    lines.append(f"- `\"local\"` → Save to local files only ({home_display}/cron/output/)")
    for platform, label in sorted(home_channels.items()):
        lines.append(f"- `\"{platform}\"` → Home channel ({label})")
    lines.append("")
    lines.append("*For explicit targeting, use `\"platform:chat_id\"` format if the user provides a specific chat ID. Do not invent private IDs.*")

    return "\n".join(lines)


def ephemeral_system_prompt(
    personality_prompt: Optional[str],
    surface_context: Optional[dict] = None,
    config_data: Optional[dict] = None,
) -> str:
    """Build WebUI-only runtime instructions that are not persisted to history."""
    parts = []
    if personality_prompt:
        parts.append(str(personality_prompt).strip())
    surface_prompt = surface_context_prompt(surface_context)
    if surface_prompt:
        parts.append(surface_prompt)
    parts.append(PROGRESS_PROMPT)
    delivery_prompt = delivery_context_prompt(config_data)
    if delivery_prompt:
        parts.append(delivery_prompt)
    return "\n\n".join(part for part in parts if part)


def _escape_workspace_prefix_path(path: str) -> str:
    return str(path or '').replace('\\', '\\\\').replace(']', '\\]')


def workspace_prefix(path: str) -> str:
    return f"[Workspace::v1: {_escape_workspace_prefix_path(path)}]\n"


def workspace_system_message(workspace: str) -> str:
    """The system message for a chat turn in a session created in *workspace*."""
    return (
        f"Active workspace at session start: {workspace}\n"
        "Every user message is prefixed with [Workspace::v1: /absolute/path] indicating the "
        "workspace the user has selected in the web UI at the time they sent that message. "
        "This tag is the single authoritative source of the active workspace and updates "
        "with every message. It overrides any prior workspace mentioned in this system "
        "prompt, memory, or conversation history. Always use the value from the most recent "
        "[Workspace::v1: ...] tag as your default working directory for ALL file operations: "
        "write_file, read_file, search_files, terminal workdir, and patch. "
        "Never fall back to a hardcoded path when this tag is present."
    )


def personality_prompt(name, config_data: Optional[dict]) -> Optional[str]:
    """The prompt text of personality *name* from ``agent.personalities``, or None.

    Matches Hermes Agent's CLI: a dict personality gives its ``system_prompt``
    (or ``prompt``) plus ``Tone:``/``Style:`` lines; anything else is its text.
    """
    if not name or not isinstance(config_data, dict):
        return None
    personalities = (config_data.get("agent") or {}).get("personalities", {})
    if not isinstance(personalities, dict) or name not in personalities:
        return None
    value = personalities[name]
    if not isinstance(value, dict):
        return str(value)
    parts = [value.get("system_prompt", "") or value.get("prompt", "")]
    if value.get("tone"):
        parts.append(f"Tone: {value['tone']}")
    if value.get("style"):
        parts.append(f"Style: {value['style']}")
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class TurnPrompts:
    """What a chat turn is told: the persisted system message, the ephemeral
    (not persisted) system prompt, and the prefix on the user's message."""

    system_message: str
    ephemeral_system_prompt: str
    user_prefix: str


def turn_prompts(session, *, session_id: str, config_data: Optional[dict]) -> TurnPrompts:
    """The prompts for a chat turn in *session*.

    The system message and the surface context name the Workspace the session
    was created in (the live one when it has none); the user prefix names the
    live Workspace.
    """
    live_workspace = str(getattr(session, "workspace", "") or "")
    frozen_workspace = getattr(session, "created_workspace", None) or live_workspace
    return TurnPrompts(
        system_message=workspace_system_message(frozen_workspace),
        ephemeral_system_prompt=ephemeral_system_prompt(
            personality_prompt(getattr(session, "personality", None), config_data),
            surface_context={
                "source": "webui",
                "session_id": session_id,
                "profile": getattr(session, "profile", None),
                "workspace": frozen_workspace,
            },
            config_data=config_data,
        ),
        user_prefix=workspace_prefix(live_workspace),
    )


def webui_agent(agent_class, bundle, *, model, session_id, toolsets):
    """A WebUI agent: the WebUI platform (so Hermes Agent adds no CLI guidance),
    quiet mode, *toolsets*, and the resolved provider *bundle* -- its provider,
    base URL and key, plus the extra arguments the agent class accepts."""
    return agent_class(
        model=model,
        provider=bundle["provider"],
        base_url=bundle["base_url"],
        api_key=bundle["api_key"],
        platform="webui",
        quiet_mode=True,
        enabled_toolsets=toolsets,
        session_id=session_id,
        **agent_bundle_kwargs(agent_class, bundle),
    )


# The constructor-routing fields that travel with provider/base_url/api_key as
# one authority. Mirrors ``api.streaming._RUNTIME_BUNDLE_FIELDS`` and
# ``api.config.CUSTOM_CONNECTION_SIDE_FIELDS``.
AGENT_BUNDLE_SIDE_FIELDS = ("api_mode", "acp_command", "acp_args", "credential_pool")


def agent_bundle_kwargs(agent_cls, bundle):
    """Return the bundle's side-field kwargs supported by ``agent_cls``.

    ``api_mode``/``acp_command``/``acp_args``/``credential_pool`` were added to
    AIAgent over several releases, so gate each on the constructor signature the
    way the streaming path does rather than raising TypeError against an older
    hermes-agent build. Values come from the BUNDLE, never from the runtime
    provider dict: a custom-provider override clears these, and reading them off
    the runtime would re-introduce the authority the merge just replaced.
    """
    import inspect as _inspect

    try:
        params = set(_inspect.signature(agent_cls.__init__).parameters)
    except (TypeError, ValueError):
        return {}
    return {
        field: bundle[field]
        for field in AGENT_BUNDLE_SIDE_FIELDS
        if field in params
    }
