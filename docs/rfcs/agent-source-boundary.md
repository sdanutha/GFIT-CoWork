# Agent Source Boundary and API Decoupling Inventory

- **Status:** Proposed
- **Created:** 2026-05-17
- **Tracking issue:** [#2453](https://github.com/nesquena/hermes-webui/issues/2453)

## Problem

The WebUI currently depends on Hermes Agent Python source being importable at
runtime. In local installs this usually means a neighboring checkout; in the
multi-container Docker setup it means the WebUI reads the `hermes-agent-src`
volume that the agent container also uses.

That source mount is a compatibility bridge, not the desired long-term contract.
Even when mounted read-only on the WebUI side, it couples WebUI releases to
Hermes Agent internal module layout and makes the multi-container setup look more
isolated than it really is.

## Current safety posture

- The multi-container compose files mount `hermes-agent-src` read-only into the
  WebUI service by default.
- `docker_init.bash` prunes the agent source subtree from `chown` so read-only
  mounts do not break startup.
- If an operator overrides the compose files with a mutable agent-source mount,
  startup now emits a notable warning. The WebUI still starts because local dev
  checkouts and custom deployments may intentionally be writable, but the warning
  makes the reduced boundary explicit.

## Source-access inventory

These are the current WebUI capabilities that still rely on Agent source or
`hermes_cli`/`agent` modules being importable. Each item should eventually move
behind an explicit, versioned Agent API or a packaged library contract that does
not require mounting the live source checkout.

| WebUI capability | Current dependency | Desired API / contract | Notes |
|---|---|---|---|
| Browser chat execution | `run_agent.AIAgent` imported by `api/streaming.py` | Run lifecycle API: start, observe, status, cancel, approval, clarify, final usage | Covered by the runtime-adapter migration in [#1925](https://github.com/nesquena/hermes-webui/issues/1925), but still source-backed today. |
| Runtime event rendering | WebUI callbacks around Agent token/reasoning/tool events | Stable event envelope for tokens, reasoning, progress, tool lifecycle, approvals, clarify, errors, and final usage | The existing run-adapter RFC describes the browser-facing shape; Agent still needs a durable producer contract. |
| Profile list/create/delete/seed | `hermes_cli.profiles` from `api/profiles.py` | Profile management API with profile metadata, env/runtime context, seed/delete operations, and validation errors | WebUI has fallback filesystem handling for some operations, but feature parity follows Hermes CLI internals. |
| Goal command state | `hermes_cli.goals` from `api/goals.py` | Goal CRUD/control API: get, save, pause/resume/clear, and status | Should preserve current `/goal` WebUI behavior without direct module import. |
| Slash command registry and plugin commands | `hermes_cli.commands` and `hermes_cli.plugins` from `api/commands.py` | Command/plugin capability discovery API scoped by active profile | WebUI should render command help from a stable capability response. |
| Provider/auth/model catalogs | `hermes_cli.models`, `hermes_cli.auth`, `hermes_cli.auth_nous`, `hermes_cli.auth_constants`, and `agent.credential_pool` from `api/config.py`; the Profile-scoped Nous/Copilot catalogs bind the private `hermes_cli.models._configured_relay_base_url`, `_chat_catalog_rows`, `_first_exchangeable_copilot_token`, `_fetch_github_models`, `_PROVIDER_MODELS` and `hermes_cli.auth_nous._agent_key_is_usable` (each guarded; a missing one degrades to the curated list); read-only availability (`_read_only_available_providers`) binds `hermes_cli.models.CANONICAL_PROVIDERS` and the private `_provider_has_credentials`, `hermes_cli.auth._registry_lookup`, `_STATUS_BY_AUTH_TYPE`, `read_credential_pool`, `hermes_cli.auth_nous.get_nous_auth_status_local`, `hermes_cli.auth_qwen.resolve_qwen_runtime_credentials` and `hermes_cli.auth_plugin_providers.PLUGIN_MIRRORED_PROVIDERS` (a missing one leaves provider detection to the WebUI's environment fallback) | Provider registry, model catalog, auth status, OAuth/credential-pool status APIs | WebUI has static fallbacks, but exact parity and custom provider state come from Agent internals. Credential discovery (model list, live models, provider detection) reads the request Profile's own `auth.json` and never calls `load_pool`/`select`, which seed and persist; provider detection also lists a provider that only the root Profile's pool has (read as it is), where the Agent's `read_credential_pool` would fall back to it (ticket 12), while keys stay the Profile's own; only the root Profile's recovery reads outside a read-only scope still use `load_pool`. The Nous and Copilot catalogs list with the request Profile's own credentials only (`_provider_catalog_ids`): no Nous refresh/persist, no host `gh`/`~/.copilot` or root-Profile credentials for a named Profile. Availability does not call `list_available_providers()`/`get_auth_status()` for Nous, Qwen or OAuth plugin providers (they refresh, persist or seed); it uses their read-only checks and may still follow the root/shared/host logins (ticket 12). |
| Redaction helper parity | `agent.redact.redact_sensitive_text` from `api/helpers.py` | Redaction service/library contract with signature/version compatibility | WebUI keeps a fallback redactor because this import has changed before. |
| CLI/Gateway session bridge | Agent `state.db` schema and gateway metadata read by sidebar/session helpers | Session listing/transcript/metadata API for non-WebUI-originated sessions | Direct SQLite/schema coupling should narrow over time, especially for messaging/email/gateway sessions. |

## Decoupling task list

1. Keep the Docker default safe: WebUI-side `hermes-agent-src` stays read-only in
   two- and three-container compose files.
2. Keep documenting the boundary honestly: multi-container isolates process,
   network, and resources, not filesystem/source compatibility.
3. Warn loudly when the WebUI container sees a writable agent-source mount in
   Docker, because that weakens the defense-in-depth posture.
4. Convert runtime execution first through the #1925 RuntimeAdapter path instead
   of adding new direct imports.
5. For each inventory row, file or link a follow-up that defines the Agent API
   response shape before replacing the import.
6. Do not claim the source mount can be removed until chat execution, provider
   catalogs/auth status, profiles, goals, commands/plugins, redaction, and
   imported Agent/Gateway sessions all have stable replacement contracts.

## Non-goals for this slice

- Do not remove `HERMES_WEBUI_AGENT_DIR`.
- Do not break local source-checkout development.
- Do not fail startup solely because the agent source is writable.
- Do not replace the runtime adapter or Hermes Agent API in this document-only
  inventory slice.
