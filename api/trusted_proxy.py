"""GFIT-CoWork -- the trusted proxy: whose address is this request from?

A request's socket peer is its address, unless the peer is a trusted proxy
(loopback, or an address in ``HERMES_WEBUI_TRUSTED_PROXY_CIDRS``) speaking for
the client in ``X-Forwarded-For``. Used by the login rate limit (api.login).
"""
import os


def peer_address(handler) -> str:
    try:
        address = getattr(handler, "client_address", None)
        if address:
            return str(address[0] or "")
    except Exception:
        pass
    return ""


def _trusted_proxy_networks():
    """Networks whose socket peer is allowed to assert a forwarded client IP.

    Loopback is ALWAYS trusted implicitly (the common same-host reverse-proxy
    deployment). Operators fronting the WebUI with a LAN/remote proxy add its
    address(es) via HERMES_WEBUI_TRUSTED_PROXY_CIDRS (comma-separated CIDRs or
    bare IPs). Malformed entries are skipped, never widening trust.
    """
    import ipaddress

    nets = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("::ffff:127.0.0.0/104"),
    ]
    raw = os.getenv("HERMES_WEBUI_TRUSTED_PROXY_CIDRS", "") or ""
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            # Invalid CIDR/IP → skip (fail closed: never widens trust).
            continue
    return nets


def _ip_in_networks(addr, networks) -> bool:
    """Family-aware membership test.

    Checks the parsed address against each network, and — for an IPv4-mapped
    IPv6 address (e.g. ``::ffff:10.9.9.9``) — ALSO checks its embedded IPv4 form
    against IPv4 networks. Without this, a mapped-IPv6 proxy peer would never
    match an IPv4 CIDR allowlist: the trusted proxy would be treated as
    untrusted (locking out legitimate clients behind it) and, inside an XFF
    chain, a mapped trusted hop would be mis-returned as the client (admitting a
    public client that preceded it). See #5764.
    """
    candidates = [addr]
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    for cand in candidates:
        for net in networks:
            try:
                if cand in net:
                    return True
            except TypeError:
                # IPv4/IPv6 family mismatch between candidate and net → skip.
                continue
    return False


def peer_is_trusted_proxy(handler) -> bool:
    """True when the immediate socket peer is loopback or an allowlisted proxy.

    Only such a peer is allowed to assert a forwarded client IP. Judged on the
    RAW socket address (never a header), so it cannot be spoofed.
    """
    import ipaddress

    raw = peer_address(handler)
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return _ip_in_networks(addr, _trusted_proxy_networks())


def forwarded_client_address(handler):
    """Resolve the real client IP from a chain fronted by a trusted proxy.

    Precondition: the caller has verified the raw socket peer is a trusted proxy.
    Consumes ALL X-Forwarded-For values (across repeated headers), preserves wire
    order, walks RIGHT-TO-LEFT skipping hops that are themselves trusted-proxy
    addresses, and returns the first non-trusted (i.e. real-client) hop. Falls
    back to X-Real-IP, then the raw socket peer. Returns None when the chain is
    present-but-empty / malformed so the caller fails closed.
    """
    import ipaddress

    try:
        xff_values = handler.headers.get_all("X-Forwarded-For") or []
    except AttributeError:
        single = handler.headers.get("X-Forwarded-For", "")
        xff_values = [single] if single else []

    hops: list[str] = []
    for header_value in xff_values:
        for token in str(header_value or "").split(","):
            hops.append(token.strip())

    if xff_values:
        # A present-but-empty / all-blank XFF is malformed → fail closed.
        if not any(hops):
            return None
        trusted_nets = _trusted_proxy_networks()

        def _is_trusted_hop(ip_str: str) -> bool:
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            return _ip_in_networks(addr, trusted_nets)

        for hop in reversed(hops):
            if not hop:
                # An empty hop inside the chain is malformed → fail closed
                # rather than skip past it (an attacker could inject blanks).
                return None
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                # Non-IP token in the chain → malformed → fail closed.
                return None
            if _is_trusted_hop(hop):
                continue
            return hop
        # Every hop was a trusted proxy → no distinct client; treat as the proxy
        # tier itself (loopback/private), i.e. resolve to the raw peer below.
        return peer_address(handler)

    real_ip = handler.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    # No forwarded header at all → the trusted proxy is speaking for itself.
    return peer_address(handler)
