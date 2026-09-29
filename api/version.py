"""
GFIT-CoWork -- running versions of GFIT-CoWork and Hermes Agent.

Resolved once at import time for the Settings version badge, static-asset
cache-busting and the stale-client check. A Deployment is upgraded by
rebuilding its image (``deploy/README.md``), so nothing here checks for or
applies updates.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from api.config import REPO_ROOT
from api.subprocess_utils import windows_hide_flags

# Lazy -- may be None if agent not found
try:
    from api.config import _AGENT_DIR
except ImportError:
    _AGENT_DIR = None


def _run_git(args, cwd, timeout=10):
    """Run a git command and return (useful output, ok).

    On failure, returns stderr (or stdout as fallback) so callers can
    surface actionable git error messages instead of empty strings.
    """
    git_executable = _resolve_git_executable()
    if not git_executable:
        return 'git executable not found', False
    try:
        r = subprocess.run(
            [git_executable] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8',
            errors='replace',
            creationflags=windows_hide_flags(),
        )
        # On non-UTF-8 locales (e.g. Chinese Windows GBK), a binary git
        # output that fails to decode used to leave r.stdout = None and crash
        # the whole import with AttributeError. Guard against None defensively.
        stdout = (r.stdout or '').strip()
        stderr = (r.stderr or '').strip()
        if r.returncode == 0:
            return stdout, True
        return stderr or stdout or f"git exited with status {r.returncode}", False
    except subprocess.TimeoutExpired as exc:
        detail = (getattr(exc, 'stderr', None) or getattr(exc, 'stdout', None) or '').strip()
        return detail or f"git {' '.join(args)} timed out after {timeout}s", False
    except FileNotFoundError:
        return 'git executable not found', False
    except OSError as exc:
        return f'git failed to start: {exc}', False



def _windows_git_from_registry():
    """Best-effort resolve git.exe from the Git-for-Windows registry key.

    Git for Windows records its install root at
    ``HKLM\\SOFTWARE\\GitForWindows\\InstallPath`` (and the WOW6432Node mirror
    for a 32-bit install on 64-bit Windows). ``git.exe`` lives under
    ``<InstallPath>\\cmd\\git.exe``. This is the reliable way to find git when
    it is installed but NOT on the launching process's PATH — e.g. the WebUI
    server started from a venv python whose environment does not inherit the
    interactive shell PATH, which otherwise degrades WEBUI_VERSION to
    ``'unknown'`` and freezes the ``?v=`` static-asset cache-busting stamp.
    """
    try:
        import winreg
    except ImportError:
        return None
    for hive, flag in (
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ):
        try:
            with winreg.OpenKey(
                hive, r'SOFTWARE\GitForWindows', 0,
                winreg.KEY_READ | flag,
            ) as key:
                install_path, _ = winreg.QueryValueEx(key, 'InstallPath')
        except OSError:
            continue
        if not install_path:
            continue
        candidate = os.path.join(install_path, 'cmd', 'git.exe')
        if os.path.exists(candidate):
            return candidate
    return None


def _resolve_git_executable():
    git_executable = shutil.which('git')
    if git_executable:
        return git_executable
    if sys.platform == 'darwin' and os.path.exists('/usr/bin/git'):
        return '/usr/bin/git'
    if sys.platform == 'win32':
        from_registry = _windows_git_from_registry()
        if from_registry:
            return from_registry
        for candidate in (
            os.path.expandvars(r'%ProgramFiles%\Git\cmd\git.exe'),
            os.path.expandvars(r'%ProgramFiles(x86)%\Git\cmd\git.exe'),
            os.path.expandvars(r'%LocalAppData%\Programs\Git\cmd\git.exe'),
        ):
            if candidate and os.path.exists(candidate):
                return candidate
    return None


def _dirty_suffix(path: Path, timeout=1) -> str:
    """Return a best-effort ``-dirty`` suffix without blocking version display."""
    out, ok = _run_git(['diff-index', '--quiet', 'HEAD', '--'], path, timeout=timeout)
    if ok:
        return ""
    # Only diff-index status 1 means dirty. Keep version display consistent
    # with the strict action-time probe; all other failures suppress the suffix.
    if out != 'git exited with status 1':
        return ""
    diff, diff_ok = _run_git(['diff', '--binary', 'HEAD', '--'], path, timeout=timeout)
    if diff_ok and diff:
        digest = hashlib.sha1(diff.encode('utf-8', errors='replace')).hexdigest()[:8]
        return f"-dirty-{digest}"
    return "-dirty"


def _describe_git_version(path: Path, *, timeout=5, dirty_timeout=1) -> str | None:
    """Return a fast git version string for a checkout, if available."""
    out, ok = _run_git(['describe', '--tags', '--always'], path, timeout=timeout)
    if not (ok and out):
        return None
    return out + _dirty_suffix(path, timeout=dirty_timeout)


def _detect_webui_version() -> str:
    """Detect the running WebUI version from git or installed fallback files.

    Resolution order:
      1. ``git describe --tags --always --dirty`` — works in any git checkout.
         Returns the exact tag on tagged commits (e.g. ``v0.50.124``), a
         post-tag descriptor between releases (e.g. ``v0.50.124-1-ge91325d``),
         or a bare SHA when no tags exist (shallow clones, fresh forks).
      2. ``api/_version.py`` — a fallback written by the Docker / CI release
         workflow when ``.git`` is not present in the image.  Expected to define
         ``__version__ = 'vX.Y.Z'``.
      3. ``api/_scm_version.py`` — setuptools-scm output in an installed wheel.
         Its PEP 440 value is normalized to the channel-neutral ``v...`` form.
      4. ``'unknown'`` — last resort; displayed as-is in the settings badge.
    """
    # Timeout capped at 3s: git describe on a healthy local repo is <50ms;
    # a 10s stall on import (NFS-mounted .git, broken git binary) is unacceptable.
    out = _describe_git_version(REPO_ROOT)
    if out:
        return out

    # Docker / baked-image fallback: api/_version.py written by CI at build time.
    # Parse with regex rather than exec() — the file holds exactly one assignment
    # and regex is sufficient; exec() on a build artifact is an unnecessary surface.
    version_file = REPO_ROOT / 'api' / '_version.py'
    if version_file.exists():
        try:
            import re as _re
            m = _re.search(
                r"""__version__\s*=\s*['"]([^'"]+)['"]""",
                version_file.read_text(encoding='utf-8'),
            )
            if m:
                return m.group(1)
        except Exception:
            pass

    # Installed-wheel fallback: setuptools-scm writes a generated module that
    # is separate from the Docker-owned _version.py contract above.
    try:
        from api._scm_version import __version__ as scm_version
        scm_version = str(scm_version).strip()
        if scm_version:
            return scm_version if scm_version.startswith(('v', 'exp-v')) else f'v{scm_version}'
    except Exception:
        pass

    return 'unknown'


def _read_agent_source_version(agent_dir: Path) -> str | None:
    """Read Hermes Agent's package version from a copied source tree."""
    init_file = agent_dir / 'hermes_cli' / '__init__.py'
    try:
        text = init_file.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    m = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def _gateway_health_base_url() -> str:
    """Return the configured/default Hermes Agent gateway base URL."""
    raw = (
        os.environ.get('GATEWAY_HEALTH_URL')
        or os.environ.get('HERMES_GATEWAY_HEALTH_URL')
        or 'http://hermes-agent:8642'
    ).strip()
    if raw.endswith('/health/detailed'):
        raw = raw[: -len('/health/detailed')]
    elif raw.endswith('/health'):
        raw = raw[: -len('/health')]
    return raw.rstrip('/')


def _version_from_gateway_health_payload(payload: object) -> str | None:
    """Extract a version string from a Hermes Agent gateway health payload."""
    if not isinstance(payload, dict):
        return None
    for key in ('version', 'agent_version', 'hermes_version'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = payload.get('agent')
    if isinstance(nested, dict):
        value = nested.get('version')
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _detect_agent_version_from_gateway_health(timeout: float = 0.75) -> str | None:
    """Best-effort cross-container gateway API fallback for Agent version."""
    base = _gateway_health_base_url()
    if not base:
        return None
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    for path in ('/health', '/health/detailed'):
        try:
            with urllib.request.urlopen(f'{base}{path}', timeout=timeout) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        version = _version_from_gateway_health_payload(payload)
        if version:
            return version
    return None


def _detect_agent_version() -> str:
    """Detect the running Hermes Agent version for UI display."""
    agent_dir = Path(_AGENT_DIR) if _AGENT_DIR is not None else None

    if agent_dir is not None:
        version_file = agent_dir / "VERSION"
        try:
            if version_file.exists():
                text = version_file.read_text(encoding='utf-8').strip()
                if text:
                    return text
        except Exception:
            pass

        # Fallback: infer from git describe when the checkout exists but no VERSION
        # file is available (common in source checkouts and developer environments).
        if agent_dir.exists():
            # Symmetric with _detect_webui_version() above — `--dirty` flags a
            # locally-modified checkout so operators can see when their agent has
            # uncommitted changes vs a clean tag. Per Opus advisor on stage-293.
            out = _describe_git_version(agent_dir)
            if out:
                return out

            # Docker two-container deployments often mount a copied agent source
            # tree without .git metadata or a VERSION file.  The package version
            # still lives in hermes_cli/__init__.py, so prefer that before giving
            # up or relying on a live gateway probe.
            source_version = _read_agent_source_version(agent_dir)
            if source_version:
                return source_version

    gateway_version = _detect_agent_version_from_gateway_health()
    if gateway_version:
        return gateway_version

    return 'not detected'


# Resolved once at import time — tags cannot change without a process restart.
WEBUI_VERSION: str = _detect_webui_version()
AGENT_VERSION: str = _detect_agent_version()
