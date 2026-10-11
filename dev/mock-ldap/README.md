# Mock LDAP for GFIT-CoWork development

GFIT-CoWork checks passwords against the company AD. On your own machine, use
this OpenLDAP server instead. It speaks LDAPS and StartTLS with a throwaway CA,
and holds three sample users.

| Employee ID | Password         | Display name  |
|-------------|------------------|---------------|
| 521740      | `Somchai-Pass-1` | สมชาย ใจดี     |
| 671278      | `Wipa-Pass-1`    | วิภา รักงาน    |
| 600001      | `Manee-Pass-1`   | มานี มีสุข    |

## Start it

```sh
cd dev/mock-ldap
./make-certs.sh          # once: writes certs/ (git-ignored)
docker compose up -d
```

LDAPS listens on `localhost:1636`, plain LDAP (for StartTLS) on `localhost:1389`.

## Point GFIT-CoWork at it

```sh
export HERMES_WEBUI_DIRECTORY=ldap
export HERMES_WEBUI_LDAP_URL=ldaps://localhost:1636
export HERMES_WEBUI_LDAP_CA_CERT="$PWD/dev/mock-ldap/certs/ca.crt"
# OpenLDAP binds by DN; the company AD uses `upn` or `domain` instead.
export HERMES_WEBUI_LDAP_BIND_FORMAT='uid={username},ou=people,dc=gfit,dc=local'
export HERMES_WEBUI_LDAP_BASE_DN='ou=people,dc=gfit,dc=local'
export HERMES_WEBUI_LDAP_USER_FILTER='(uid={username})'
```

Then create a Profile for a sample user and log in as them:

```sh
python3 -m api.operator_cli create 521740 --clone-from default
```

Everyone is a User; there is no Admin to configure (ADR 0006).

To try StartTLS instead, use `HERMES_WEBUI_LDAP_URL=ldap://localhost:1389` and
`HERMES_WEBUI_LDAP_STARTTLS=1`. Plain `ldap://` without StartTLS is refused.

## Run the LDAP tests against it

The tests in `tests/test_gfit08_ldap_directory.py` that need a server are
skipped unless `GFIT_MOCK_LDAP_URL` is set:

```sh
GFIT_MOCK_LDAP_URL=ldaps://localhost:1636 ./scripts/test.sh tests/test_gfit08_ldap_directory.py
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
