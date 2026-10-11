# No hosted CI; the local test runner is the gate

GFIT-CoWork has no GitHub Actions and no `.github/` folder. The suite runs locally through `./scripts/test.sh` before a merge, and that run is the only automated gate. We chose this over keeping the workflows because the repo keeps its tooling in one place, the Claude Code layout (`AGENTS.md` and `.claude/`), and the owner merges locally into `main`, so a hosted check after the push guarded nothing a local run does not.

Supersedes in part ADR 0005 (the native-Windows CI check and the Docker smoke workflow).

## Consequences

- Nothing runs on push or pull request. A green check on GitHub is not evidence; the evidence is the local run reported in the PR body or merge commit.
- The checks that only CI ran are now manual: the ruff diff gate (`scripts/ruff_lint.py --diff`), the headless browser smoke (`tests/browser_smoke.py`), the conversation-lifecycle browser rows, the Docker smoke of `deploy/`, and `start.ps1` on native Windows. Run the ones a change touches.
- Tests that read workflow files are gone. Git history keeps the workflows if hosted CI is ever wanted back.
