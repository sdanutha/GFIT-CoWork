# The mock Directory for Development Deployments

GFIT-CoWork checks passwords against the company AD. A Development Deployment
(ADR 0009) uses this OpenLDAP server instead: `deploy/new-deployment --dev`
makes a throwaway CA and server certificate for it, and the development overlay
(`deploy/docker-compose.dev.yml`) runs it as the `mock-ldap` service with the
sample users in `users.ldif`. It speaks LDAPS and StartTLS.

| Employee ID | Password         | Display name  |
|-------------|------------------|---------------|
| 521740      | `Somchai-Pass-1` | สมชาย ใจดี     |
| 671278      | `Wipa-Pass-1`    | วิภา รักงาน    |
| 600001      | `Manee-Pass-1`   | มานี มีสุข    |

These passwords are public, which is why a Development Deployment publishes
its ports on `127.0.0.1` unless its `.env` sets `DEV_BIND`.

## Use it

```sh
deploy/new-deployment --dev dev 0
deploy/cowork dev up -d
deploy/cowork dev exec -T -u hermeswebui -w /app gfit-cowork \
  /app/venv/bin/python -m api.operator_cli create 521740 --clone-from default
```

Then log in at `http://localhost:8787` as `521740` / `Somchai-Pass-1`.
Everyone is a User; there is no Admin to configure (ADR 0006).

GFIT-CoWork reaches it as `ldaps://mock-ldap:636`, with the values
`new-deployment --dev` writes into the Deployment's `.env`:

```sh
HERMES_WEBUI_LDAP_URL=ldaps://mock-ldap:636
# OpenLDAP binds by DN; the company AD uses `upn` or `domain` instead.
HERMES_WEBUI_LDAP_BIND_FORMAT=uid={username},ou=people,dc=gfit,dc=local
HERMES_WEBUI_LDAP_BASE_DN=ou=people,dc=gfit,dc=local
HERMES_WEBUI_LDAP_USER_FILTER=(uid={username})
HERMES_WEBUI_LDAP_CA_CERT=/certs/ad-ca.crt
```

To try StartTLS instead, use `HERMES_WEBUI_LDAP_URL=ldap://mock-ldap:389` and
`HERMES_WEBUI_LDAP_STARTTLS=1`. Plain `ldap://` without StartTLS is refused.

## Run the LDAP tests against it

The tests in `tests/test_gfit08_ldap_directory.py` that need a server are
skipped unless `GFIT_MOCK_LDAP_URL` is set. The test container reaches a
running Development Deployment's mock Directory through the host
(`LDAPS_PORT` and `LDAP_PORT` in its `.env`, 1636 and 1389 for N=0):

```sh
GFIT_MOCK_LDAP_URL=ldaps://host.docker.internal:1636 \
GFIT_MOCK_LDAP_PLAIN_URL=ldap://host.docker.internal:1389 \
GFIT_MOCK_LDAP_CA_CERT=deploy/deployments/dev/certs/mock-ldap/ca.crt \
  ./scripts/test.sh tests/test_gfit08_ldap_directory.py
```

## For the company AD

```sh
HERMES_WEBUI_LDAP_URL=ldaps://<ad-host>
HERMES_WEBUI_LDAP_BIND_FORMAT=upn          # 521740@<domain>; or `domain` for <DOMAIN>\521740
HERMES_WEBUI_LDAP_DOMAIN=<domain>
HERMES_WEBUI_LDAP_BASE_DN=<base DN to find users under>
HERMES_WEBUI_LDAP_CA_CERT=<the AD CA certificate, if not trusted by the system>
```

`HERMES_WEBUI_LDAP_USER_FILTER` defaults to `(sAMAccountName={username})`.
