"""
Issue #803 (completes #798) — per-client profile isolation via cookie + thread-local.

PR #800 fixed POST /api/session/new (client sends profile in body).
PR #805 extends the fix to ALL endpoints: profile switches set a hermes_profile
cookie, server.py reads it per-request into a thread-local, and the existing
api/profiles.py helpers consult the thread-local before the process global.

GFIT-CoWork: the hermes_profile cookie is gone with Profile switching and the
mode with login off (ADR 0006); a request's Profile is its Admission's. The
thread-local context below is how that Profile reaches the helpers.

Covers:
  2. set_request_profile() / get_active_profile_name() / clear_request_profile()
  3. get_active_hermes_home() routes via thread-local
  5. Concurrent requests on different threads see independent profiles
"""
import os
import threading



# ── 1. Cookie build/parse roundtrip ──────────────────────────────────────────

# ── 1b. Profile cookie name resolution (env > legacy env > default) ───────────

# ── 2. Thread-local request context ──────────────────────────────────────────

class TestThreadLocalProfileContext:

    def test_tls_takes_priority_over_global(self):
        import api.profiles as p
        original = p._active_profile
        try:
            p._active_profile = 'global-default'
            p.set_request_profile('alice')
            assert p.get_active_profile_name() == 'alice'
        finally:
            p.clear_request_profile()
            p._active_profile = original

    def test_global_used_when_tls_cleared(self):
        import api.profiles as p
        original = p._active_profile
        try:
            p._active_profile = 'global-default'
            p.set_request_profile('alice')
            p.clear_request_profile()
            assert p.get_active_profile_name() == 'global-default'
        finally:
            p._active_profile = original

    def test_clear_is_idempotent(self):
        import api.profiles as p
        # Calling clear on a thread that never set anything must not raise
        p.clear_request_profile()
        p.clear_request_profile()


# ── 3. get_active_hermes_home routes through TLS ─────────────────────────────

def test_get_active_hermes_home_respects_tls(tmp_path, monkeypatch):
    import api.profiles as p
    monkeypatch.setattr(p, '_DEFAULT_HERMES_HOME', tmp_path)
    profile_dir = tmp_path / 'profiles' / 'alice'
    profile_dir.mkdir(parents=True)
    try:
        p.set_request_profile('alice')
        assert p.get_active_hermes_home() == profile_dir
        p.set_request_profile('default')
        assert p.get_active_hermes_home() == tmp_path
    finally:
        p.clear_request_profile()


# ── 5. Concurrent threads see independent profile context ────────────────────

def test_concurrent_threads_see_independent_profiles():
    """The whole point of thread-local isolation: two threads, two cookies,
    two different get_active_profile_name() results, simultaneously."""
    import api.profiles as p

    results = {}
    errors = []
    barrier = threading.Barrier(2, timeout=5)

    def worker(name, key):
        try:
            p.set_request_profile(name)
            barrier.wait()  # both threads have set their TLS
            # Now each thread reads — must see its own value
            results[key] = p.get_active_profile_name()
            p.clear_request_profile()
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=('alice', 'alice'))
    t2 = threading.Thread(target=worker, args=('bob', 'bob'))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    assert not errors, f"Workers raised: {errors}"
    assert results.get('alice') == 'alice', f"alice thread saw {results.get('alice')!r}"
    assert results.get('bob') == 'bob', f"bob thread saw {results.get('bob')!r}"
