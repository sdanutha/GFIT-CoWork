# 04: Development Deployment end to end

**What to build:** new-deployment --dev creates a Development Deployment; deploy/cowork adds the dev overlay (checkout mounted, mock Directory service, plain HTTP on localhost, ports on DEV_BIND default 127.0.0.1) and refuses it unless the hostname is localhost.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] new-deployment --dev writes GFIT_DEV=1, localhost, mock Directory values and its CA
- [ ] cowork adds the overlay only when GFIT_DEV=1, and refuses a non-localhost dev Deployment
- [ ] Rendered config: source mount, ports on DEV_BIND, gateway/A2A/Dashboard published in dev only
- [ ] Seam 1 and 2 tests; setup docs
