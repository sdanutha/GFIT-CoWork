/* Login page — external script, no inline handlers.
 * Loaded by the /login route. Reads data attributes from the form for
 * i18n strings so the server does not need to inject JS literals.
 */
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('login-form');
  var input = document.getElementById('pw');
  var userField = document.getElementById('username');

  if (!form || !input || !userField) return;

  var invalidPw = form.getAttribute('data-invalid-pw') || 'Invalid password';
  var connFailed = form.getAttribute('data-conn-failed') || 'Connection failed';

  function showErr(msg) {
    var err = document.getElementById('err');
    if (err) { err.textContent = msg; err.style.display = 'block'; }
  }

  function hideErr() {
    var err = document.getElementById('err');
    if (err) { err.style.display = 'none'; }
  }

  // Return the ?next= redirect path if present and safe, otherwise './'
  // Guards against open-redirect: rejects protocol-relative (//evil.com),
  // absolute URLs, backslash variants, and control characters.
  function _safeNextPath() {
    try {
      var raw = new URL(window.location.href).searchParams.get('next');
      if (!raw) return './';
      if (raw.charAt(0) !== '/') return './';             // must be path-absolute
      if (raw.charAt(1) === '/' || raw.charAt(1) === '\\') return './'; // reject // and \\
      if (/[\x00-\x1f\x7f\s]/.test(raw)) return './';  // reject control chars / whitespace
      // #5578: never redirect back to the login page — that self-referential
      // chain is what grows the URL exponentially on repeated expired-auth
      // bounces. Detect the login route even through nested percent-encoding
      // (a nested chain looks like `/session/login%3Fnext%3D...`, where the `?`
      // is encoded so a plain split('?') wouldn't isolate the path). Decode a
      // few levels and check the leading PATH. Only collapse login-route chains
      // — a legitimate non-login path that merely carries its own `next=` query
      // key must still round-trip.
      if (raw.length > 2048) return './';
      var probe = raw;
      var stabilized = false;
      for (var i = 0; i < 8; i++) {
        var pathOnly = probe.split('?')[0].split('#')[0].split('&')[0].replace(/\/+$/, '');
        if (pathOnly === '/login' || /\/login$/.test(pathOnly)) return './';
        var decoded;
        try { decoded = decodeURIComponent(probe); } catch (_) { stabilized = true; break; }
        if (decoded === probe) { stabilized = true; break; }
        probe = decoded;
      }
      // If still decoding at the cap (pathologically deep encoding), fail closed.
      if (!stabilized) return './';
      return raw;
    } catch (_) { return './'; }
  }

  async function doLogin(e) {
    e.preventDefault();
    hideErr();
    // GFIT-CoWork Directory login: employee ID and AD password.
    var payload = { username: userField.value, password: input.value };
    try {
      var res = await fetch('api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok && data.ok) {
        window.location.href = _safeNextPath();
      } else {
        showErr(data.error || invalidPw);
      }
    } catch (ex) {
      showErr(connFailed);
    }
  }

  form.addEventListener('submit', doLogin);

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      doLogin(e);
    }
  });

  // On page load, probe the server so we can distinguish "can't reach server"
  // (Tailscale off, wrong network) from "session expired / need to log in".
  // Uses /health — public for WebUI auth, but deployment access proxies may
  // require same-origin cookies before the request reaches WebUI.
  // If unreachable, retries every 3 s and auto-reloads once the server is back.
  (function checkConnectivity() {
    var retryTimer = null;

    function setFormDisabled(disabled) {
      input.disabled = disabled;
      var btn = form.querySelector('button');
      if (btn) btn.disabled = disabled;
    }

    function probe() {
      fetch('health', { method: 'GET', credentials: 'same-origin' })
        .then(function (r) {
          if (r.ok) {
            // Server is reachable — if we were in retry mode, reload so the
            // page reflects the correct auth state (expired session, etc.).
            if (retryTimer !== null) {
              clearInterval(retryTimer);
              retryTimer = null;
              window.location.reload();
            }
          } else {
            showErr(connFailed + ' (server error ' + r.status + ')');
          }
        })
        .catch(function () {
          showErr('Cannot reach server — check your VPN / Tailscale connection.');
          setFormDisabled(true);
          // Keep retrying so the page auto-recovers once the network is back.
          if (retryTimer === null) {
            retryTimer = setInterval(probe, 3000);
          }
        });
    }

    probe();
  })();
});
