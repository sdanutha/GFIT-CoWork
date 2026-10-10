"""A loopback socket peer is always a trusted proxy; nothing else is by default.

``api.trusted_proxy.peer_is_trusted_proxy`` decides whether the raw socket peer
may assert a forwarded client address (``api.login``). Loopback is trusted
implicitly, including the IPv4-mapped IPv6 form ``::ffff:127.x.x.x``. Private,
documentation and IPv4-mapped private addresses are not, unless the Operator
lists them in ``HERMES_WEBUI_TRUSTED_PROXY_CIDRS``.
"""

import pytest

from api.trusted_proxy import peer_is_trusted_proxy


class _Peer:
    def __init__(self, address):
        self.client_address = (address, 12345)


@pytest.fixture(autouse=True)
def _no_configured_proxies(monkeypatch):
    monkeypatch.delenv("HERMES_WEBUI_TRUSTED_PROXY_CIDRS", raising=False)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.255.255.255",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:127.255.255.255",
    ],
)
def test_loopback_peer_is_a_trusted_proxy(address):
    assert peer_is_trusted_proxy(_Peer(address)) is True


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "192.168.1.1",
        "2001:db8::1",
        "::ffff:10.0.0.1",
        "::ffff:192.168.1.1",
    ],
)
def test_non_loopback_peer_is_not_a_trusted_proxy(address):
    assert peer_is_trusted_proxy(_Peer(address)) is False
