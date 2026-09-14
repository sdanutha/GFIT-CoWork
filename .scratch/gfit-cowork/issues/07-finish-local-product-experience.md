# 07: Finish the local product experience

**What to build:** GFIT CoWork becomes comfortable and safe for daily local use through responsive English-first UI, appearance preferences, retained drafts, and end-to-end protection of Hermes-owned data.

**Blocked by:** 05: Handle Hermes Approval requests; 06: Respect Live Thread ownership.

**Status:** done

- [x] The app follows the system appearance by default and supports a persistent dark-mode choice.
- [x] Layout remains usable for the Workspace list, Thread history, composer, tool activity, and Approval controls at supported desktop and narrow browser widths.
- [x] Drafts persist per selected Thread or new Workspace conversation without being sent automatically.
- [x] UI labels are English-first while prompts and rendered Hermes content remain language-neutral.
- [x] Preference storage contains no Thread transcript, tool output, credential, or Approval secret.
- [x] End-to-end tests verify the primary Workspace-to-Thread-to-prompt-to-approval journey with a fake gateway and fixture Workspace.

**Notes:** Appearance is a persisted `system|light|dark` preference applied via `data-theme` (CSS honors an explicit choice and the system default). Drafts persist per Thread under `gfit-cowork:draft:<id>` — the viewer's own unsent input, cleared on send, never a transcript/secret. The E2E test drives the full journey through `<App>` with a mocked fetch + EventSource and asserts preference storage holds only navigation (no transcript, tool output, or approval action text).
