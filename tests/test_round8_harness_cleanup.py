# -*- coding: utf-8 -*-
"""Regresiones pequeñas del harness local Round 8/9."""

from scripts.local.security_round8_staff import _is_local_loopback
from services.environment_guard_service import _is_local_db_url


def test_round8_accepts_ipv4_ipv6_loopback_and_localhost_forms():
    for value in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128", "localhost"):
        assert _is_local_loopback(value) is True


def test_round8_rejects_external_or_broad_network_forms():
    for value in ("192.168.1.1", "10.0.0.1", "8.8.8.8", "127.0.0.1/24", "::/0", "localhost.evil"):
        assert _is_local_loopback(value) is False


def test_local_db_guard_supports_ipv6_loopback_without_accepting_external_hosts():
    assert _is_local_db_url("postgresql://u:p@[::1]:5432/domestica_cibao_local") is True
    assert _is_local_db_url("postgresql://u:p@localhost:5432/domestica_cibao_local") is True
    assert _is_local_db_url("postgresql://u:p@203.0.113.10:5432/domestica_cibao_local") is False
