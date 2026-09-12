# 07: Finish the local product experience

**What to build:** GFIT CoWork becomes comfortable and safe for daily local use through responsive English-first UI, appearance preferences, retained drafts, and end-to-end protection of Hermes-owned data.

**Blocked by:** 05: Handle Hermes Approval requests; 06: Respect Live Thread ownership.

**Status:** ready-for-agent

- [ ] The app follows the system appearance by default and supports a persistent dark-mode choice.
- [ ] Layout remains usable for the Workspace list, Thread history, composer, tool activity, and Approval controls at supported desktop and narrow browser widths.
- [ ] Drafts persist per selected Thread or new Workspace conversation without being sent automatically.
- [ ] UI labels are English-first while prompts and rendered Hermes content remain language-neutral.
- [ ] Preference storage contains no Thread transcript, tool output, credential, or Approval secret.
- [ ] End-to-end tests verify the primary Workspace-to-Thread-to-prompt-to-approval journey with a fake gateway and fixture Workspace.
