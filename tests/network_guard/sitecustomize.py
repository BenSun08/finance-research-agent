"""Disable external Python networking in explicitly offline test processes."""

from __future__ import annotations

import errno
import ipaddress
import os
import socket
from typing import Any

_MESSAGE = "offline test network guard: external DNS and network access are disabled"


class OfflineTestNetworkError(OSError):
    """Raised when a guarded test process attempts external networking."""

    def __init__(self) -> None:
        super().__init__(errno.ENETUNREACH, _MESSAGE)


def _is_local_host(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str):
        return False
    try:
        address = ipaddress.ip_address(host.split("%", maxsplit=1)[0])
    except ValueError:
        hostname = host.rstrip(".").lower()
        return hostname == "localhost" or hostname.endswith(".localhost")
    return address.is_loopback


def _require_local_host(host: object) -> None:
    if not _is_local_host(host):
        raise OfflineTestNetworkError


if os.environ.get("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS") == "1":
    _original_getaddrinfo = socket.getaddrinfo
    _original_gethostbyaddr = socket.gethostbyaddr
    _original_gethostbyname = socket.gethostbyname
    _original_gethostbyname_ex = socket.gethostbyname_ex
    _original_getnameinfo = socket.getnameinfo
    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex
    _original_sendto = socket.socket.sendto

    def _guarded_getaddrinfo(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[Any, ...]]:
        _require_local_host(host)
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    def _guarded_gethostbyname(host: str | bytes) -> str:
        _require_local_host(host)
        return _original_gethostbyname(host)

    def _guarded_gethostbyname_ex(host: str | bytes) -> tuple[str, list[str], list[str]]:
        _require_local_host(host)
        return _original_gethostbyname_ex(host)

    def _guarded_gethostbyaddr(address: str | bytes) -> tuple[str, list[str], list[str]]:
        _require_local_host(address)
        return _original_gethostbyaddr(address)

    def _guarded_getnameinfo(address: Any, flags: int) -> tuple[str, str]:
        if not isinstance(address, tuple) or not address:
            raise OfflineTestNetworkError
        _require_local_host(address[0])
        return _original_getnameinfo(address, flags)

    def _guarded_connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _require_local_host(address[0] if isinstance(address, tuple) else address)
        _original_connect(self, address)

    def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _require_local_host(address[0] if isinstance(address, tuple) else address)
        return _original_connect_ex(self, address)

    def _guarded_sendto(self: socket.socket, data: bytes, *args: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6) and args:
            address = args[-1]
            _require_local_host(address[0] if isinstance(address, tuple) else address)
        return _original_sendto(self, data, *args)

    socket.getaddrinfo = _guarded_getaddrinfo
    socket.gethostbyaddr = _guarded_gethostbyaddr
    socket.gethostbyname = _guarded_gethostbyname
    socket.gethostbyname_ex = _guarded_gethostbyname_ex
    socket.getnameinfo = _guarded_getnameinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.socket.sendto = _guarded_sendto
