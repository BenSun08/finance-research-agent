"""External DNS calls are blocked through every public socket resolver."""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "tests" / "network_guard" / "sitecustomize.py"


def _load_guard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("offline_network_guard_test", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("resolver_name", "arguments"),
    (
        ("getaddrinfo", ("api.example.test", 443)),
        ("getaddrinfo", (123, 443)),
        ("gethostbyname", ("api.example.test",)),
        ("gethostbyname_ex", ("api.example.test",)),
        ("gethostbyaddr", ("203.0.113.8",)),
        ("getnameinfo", (("203.0.113.8", 443), 0)),
    ),
)
def test_guard_blocks_all_external_name_resolution_apis_before_resolving(
    monkeypatch: pytest.MonkeyPatch,
    resolver_name: str,
    arguments: tuple[Any, ...],
) -> None:
    calls: list[str] = []

    def fake_resolver(*args: Any) -> Any:
        calls.append(resolver_name)
        return None

    monkeypatch.setenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", "1")
    monkeypatch.setattr(socket, resolver_name, fake_resolver)
    guard = _load_guard_module()

    with pytest.raises(guard.OfflineTestNetworkError):
        getattr(socket, resolver_name)(*arguments)

    assert calls == []


@pytest.mark.parametrize(
    ("resolver_name", "arguments"),
    (
        ("getaddrinfo", (None, 0)),
        ("gethostbyname", (b"localhost",)),
        ("gethostbyname_ex", ("LOCALHOST.",)),
        ("gethostbyaddr", ("127.0.0.1",)),
        ("getnameinfo", (("::1", 443, 0, 0), 0)),
    ),
)
def test_guard_preserves_local_name_resolution(
    monkeypatch: pytest.MonkeyPatch,
    resolver_name: str,
    arguments: tuple[Any, ...],
) -> None:
    calls: list[str] = []

    def fake_resolver(*args: Any) -> str:
        calls.append(resolver_name)
        return "local-result"

    monkeypatch.setenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", "1")
    monkeypatch.setattr(socket, resolver_name, fake_resolver)
    _load_guard_module()

    assert getattr(socket, resolver_name)(*arguments) == "local-result"
    assert calls == [resolver_name]


@pytest.mark.parametrize(
    ("method_name", "family", "socket_type", "arguments"),
    (
        ("connect", socket.AF_INET, socket.SOCK_STREAM, (("203.0.113.8", 443),)),
        ("connect_ex", socket.AF_INET6, socket.SOCK_STREAM, (("2001:db8::8", 443, 0, 0),)),
        ("sendto", socket.AF_INET, socket.SOCK_DGRAM, (b"test", ("203.0.113.8", 53))),
    ),
)
def test_guard_blocks_external_socket_methods_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    family: int,
    socket_type: int,
    arguments: tuple[Any, ...],
) -> None:
    calls: list[str] = []

    def fake_socket_method(self: socket.socket, *args: Any) -> int:
        calls.append(method_name)
        return 0

    monkeypatch.setenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", "1")
    monkeypatch.setattr(socket.socket, method_name, fake_socket_method)
    guard = _load_guard_module()
    connection = socket.socket(family, socket_type)
    try:
        with pytest.raises(guard.OfflineTestNetworkError):
            getattr(connection, method_name)(*arguments)
    finally:
        connection.close()

    assert calls == []


@pytest.mark.parametrize(
    ("method_name", "family", "socket_type", "arguments", "expected"),
    (
        ("connect", socket.AF_INET, socket.SOCK_STREAM, (("127.0.0.1", 0),), None),
        ("connect", socket.AF_UNIX, socket.SOCK_STREAM, ("local-socket",), None),
        ("connect_ex", socket.AF_INET6, socket.SOCK_STREAM, (("::1", 0, 0, 0),), 0),
        ("connect_ex", socket.AF_UNIX, socket.SOCK_STREAM, ("local-socket",), 0),
        ("sendto", socket.AF_INET, socket.SOCK_DGRAM, (b"test", ("127.0.0.1", 53)), 0),
        ("sendto", socket.AF_INET, socket.SOCK_DGRAM, (b"test",), 0),
        ("sendto", socket.AF_UNIX, socket.SOCK_DGRAM, (b"test",), 0),
    ),
)
def test_guard_preserves_loopback_and_unix_socket_methods(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    family: int,
    socket_type: int,
    arguments: tuple[Any, ...],
    expected: int | None,
) -> None:
    calls: list[str] = []

    def fake_socket_method(self: socket.socket, *args: Any) -> int:
        calls.append(method_name)
        return 0

    monkeypatch.setenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", "1")
    monkeypatch.setattr(socket.socket, method_name, fake_socket_method)
    _load_guard_module()
    connection = socket.socket(family, socket_type)
    try:
        assert getattr(connection, method_name)(*arguments) == expected
    finally:
        connection.close()

    assert calls == [method_name]


def test_guard_rejects_malformed_nameinfo_input_without_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_resolver(*args: Any) -> tuple[str, str]:
        calls.append("getnameinfo")
        return "", ""

    monkeypatch.setenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", "1")
    monkeypatch.setattr(socket, "getnameinfo", fake_resolver)
    guard = _load_guard_module()

    with pytest.raises(guard.OfflineTestNetworkError):
        socket.getnameinfo("203.0.113.8", 0)  # type: ignore[arg-type]

    assert calls == []


def test_guard_is_inactive_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FINANCE_RESEARCH_AGENT_OFFLINE_TESTS", raising=False)
    original = socket.getaddrinfo

    _load_guard_module()

    assert socket.getaddrinfo is original
