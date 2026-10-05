"""GFIT-CoWork -- the Operator's command line for the Profile lifecycle.

There is no Admin in the web app (ADR 0006). The Operator, on the Deployment's
server, runs this with the same Python and environment as the web server::

    python3 -m api.operator_cli list
    python3 -m api.operator_cli create 521740 --display-name "Somchai Jaidee"
    python3 -m api.operator_cli disable 521740
    python3 -m api.operator_cli enable 521740
    python3 -m api.operator_cli delete 521740 --confirm 521740
    python3 -m api.operator_cli sessions-audit
    python3 -m api.operator_cli sessions-repair
    python3 -m api.operator_cli sessions-cleanup [--empty]

It reads the repo ``.env`` first, as the launcher does, so it finds the same
state directory and Hermes home as the server. Each action is the matching
function in ``api.roster``. This process changes only durable state (the
roster, the Hermes Profile, the Profile's scheduled jobs); the running server
notices a disable and ends the Profile's logins and running turns itself
(``api.roster_watch``). Delete refuses a Profile that is not already disabled.

The ``sessions-*`` actions look after the web app's session store:
``sessions-audit`` is read-only; ``sessions-repair`` and ``sessions-cleanup``
change session files and the index, so stop the server first.
It never prepares or installs the Hermes Agent runtime; start the server once
first so the runtime is ready.

Exit status: 0 done, 1 refused (or a repair left problems), 2 bad usage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _load_env() -> None:
    """Load the repo ``.env`` into the environment, as ``bootstrap.py`` does."""
    try:
        import bootstrap  # noqa: F401  (loads .env on import)
    except ImportError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m api.operator_cli", description="Manage this Deployment's Profiles.")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="list the Profiles with their status and last login")
    create = actions.add_parser("create", help="create a Profile for a User, named after their employee ID")
    create.add_argument("name")
    create.add_argument("--display-name", default="", help="shown until the User first logs in")
    create.add_argument("--clone-from", default=None, help="copy skills and config from this Profile")
    for action, text in (
        ("disable", "stop a Profile: its User cannot log in and its work stops; its data stays"),
        ("enable", "let a disabled Profile's User log in again and resume the jobs the disable paused"),
    ):
        actions.add_parser(action, help=text).add_argument("name")
    delete = actions.add_parser("delete", help="delete a disabled Profile and all its data")
    delete.add_argument("name")
    delete.add_argument("--confirm", required=True, help="repeat the Profile name to confirm")
    actions.add_parser("sessions-audit", help="report session-store problems (read-only)")
    actions.add_parser("sessions-repair", help="apply the safe session-store repairs (stop the server first)")
    cleanup = actions.add_parser(
        "sessions-cleanup", help="delete untitled empty sessions and index ghosts (stop the server first)")
    cleanup.add_argument("--empty", action="store_true", help="delete every session with no messages")
    return parser


def _sessions(action: str, args) -> int:
    """The session-store maintenance actions; print the result as JSON."""
    from api import config
    from api.models import _active_state_db_path

    if action == "sessions-cleanup":
        from api.routes import cleanup_sessions

        print(json.dumps({"cleaned": cleanup_sessions(zero_only=args.empty)}))
        return 0
    from api import session_recovery

    run = session_recovery.audit_session_recovery if action == "sessions-audit" else session_recovery.repair_safe_session_recovery
    result = run(config.SESSION_DIR, state_db_path=_active_state_db_path())
    print(json.dumps(result, indent=2, default=str))
    return 0 if action == "sessions-audit" or result.get("clean") else 1


def _row(view: dict) -> str:
    last = view.get("last_login")
    if last:
        import time

        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(last))
    return f"{view['name']}\t{view['status']}\t{last or '-'}\t{view.get('display_name') or ''}"


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.action.startswith("sessions-"):
        return _sessions(args.action, args)
    from api import profiles, roster

    try:
        if args.action == "list":
            for row in profiles.list_profiles_api():
                name = row.get("name") if isinstance(row, dict) else None
                if isinstance(name, str) and not row.get("is_default"):
                    print(_row(roster.view(name)))
            return 0
        if args.action == "create":
            options = {"clone_from": args.clone_from} if args.clone_from else {}
            view = roster.create_profile(args.name, args.display_name, **options)
        elif args.action == "disable":
            view = roster.disable_profile(args.name)
        elif args.action == "enable":
            view = roster.enable_profile(args.name)
        else:
            if args.confirm != args.name:
                print("Confirm the deletion: --confirm must repeat the Profile name.", file=sys.stderr)
                return 2
            roster.delete_profile(args.name)
            print(f"{args.name}\tdeleted")
            return 0
    except roster.ProfileRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(_row(roster.view(args.name)))
    return 0


def main() -> None:
    _load_env()
    # Preparing the Hermes Agent runtime is the server's job. Without this, the
    # first import of the Agent's cron code may install into the data root and
    # re-exec this process, which then cannot find this module.
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
