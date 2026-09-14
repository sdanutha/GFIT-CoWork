# 01: Launch GFIT CoWork with Hermes health

**What to build:** A local-only GFIT CoWork web application and CoWork host that attaches to an available local Hermes gateway or starts it when needed. A developer can open the app on the same machine and see either a healthy Hermes-ready state or a safe, actionable health/setup state without entering credentials into GFIT CoWork.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] The host accepts only same-machine browser connections and does not expose a remote-access mode.
- [x] The app identifies itself as GFIT CoWork and never as Claude Code or an Anthropic product.
- [x] A healthy Hermes connection is observable from the browser through the CoWork host.
- [x] Missing, stopped, or unconfigured Hermes produces an actionable health/setup state without secret values.
- [x] Host and browser behavior are covered through the `HermesWorkspaceGateway` seam using a fake gateway and no real model invocation.
