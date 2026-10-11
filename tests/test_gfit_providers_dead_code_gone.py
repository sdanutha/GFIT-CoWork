"""api/providers.py keeps only what the web app still calls (remove-admin ticket 19).

The provider settings page and the provider quota chip left with the Admin
(ADR 0006). Nothing starts an account-usage probe any more, so its subprocess
workers, cache and the code that cleared them are gone, together with helpers
nothing calls.
"""

import ast
from pathlib import Path

import api.config as config
import api.providers as providers

REPO = Path(__file__).resolve().parents[1]

GONE = (
    "_ACCOUNT_USAGE_PARENT_DEATHSIG_BOOTSTRAP",
    "_ACCOUNT_USAGE_SUBPROCESS_CODE",
    "_ACCOUNT_USAGE_SUBPROCESS_TIMEOUT_SECONDS",
    "_AccountUsageProbeWorker",
    "_PROVIDER_CREDENTIAL_ENV_VARS",
    "_account_usage_payload_to_snapshot",
    "_account_usage_preexec_fn",
    "_account_usage_status_cache",
    "_account_usage_subprocess_env",
    "_account_usage_worker_pool",
    "_close_account_usage_probe_workers",
    "_entry_pool_exhausted_reason",
    "_entry_pool_retry_after",
    "_fetch_account_usage_once_for_home",
    "_iso",
    "_launch_account_usage_worker_process",
    "_safe_entry_label",
    "invalidate_account_usage_status_cache",
)


def test_the_account_usage_probe_is_gone():
    for name in GONE:
        assert not hasattr(providers, name), name
    src = (REPO / "api" / "providers.py").read_text(encoding="utf-8")
    assert "agent.account_usage" not in src
    assert "atexit" not in src


def test_clearing_the_credential_pool_cache_no_longer_touches_account_usage():
    src = (REPO / "api" / "config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "invalidate_credential_pool_cache"
    )
    assert "account_usage" not in ast.get_source_segment(src, fn)
    config.invalidate_credential_pool_cache("openrouter")


def test_what_the_web_app_calls_is_still_there():
    for name in (
        "_load_env_file",
        "_write_env_file",
        "_provider_has_key",
        "_provider_credential_env_vars",
        "_pool_entry_currently_unusable",
        "provider_has_usable_pool_credential",
        "provider_has_process_wakeup_recovery_credential",
    ):
        assert callable(getattr(providers, name)), name
