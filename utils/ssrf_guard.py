# -*- coding: utf-8 -*-
from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit


class OutboundURLBlocked(ValueError):
    """URL externa bloqueada por politica SSRF."""


def _is_public_ip(ip_raw: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_raw)
    except ValueError:
        return False
    return bool(ip_obj.is_global)


def _normalize_hostname(hostname: str) -> str:
    return (hostname or "").strip().lower().rstrip(".")


def _resolve_host_ips(hostname: str) -> set[str]:
    try:
        records = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        raise OutboundURLBlocked("No se pudo resolver el host externo.") from exc

    ips = set()
    for rec in records:
        sockaddr = rec[4] if len(rec) >= 5 else ()
        if not sockaddr:
            continue
        ip = str(sockaddr[0]).strip()
        if ip:
            ips.add(ip)
    if not ips:
        raise OutboundURLBlocked("Host externo sin IP resolvible.")
    return ips


def validate_external_url(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
    allowed_hosts: set[str] | None = None,
) -> str:
    raw = (url or "").strip()
    parsed = urlsplit(raw)

    scheme = (parsed.scheme or "").strip().lower()
    if scheme not in allowed_schemes:
        raise OutboundURLBlocked("Esquema no permitido para request externa.")

    if parsed.username or parsed.password:
        raise OutboundURLBlocked("Credenciales en URL externa no permitidas.")

    host = _normalize_hostname(parsed.hostname or "")
    if not host:
        raise OutboundURLBlocked("Host externo invalido.")
    if host in {"localhost", "0.0.0.0"}:
        raise OutboundURLBlocked("Host local bloqueado.")

    if allowed_hosts is not None:
        allowed = {_normalize_hostname(h) for h in allowed_hosts if h}
        if host not in allowed:
            raise OutboundURLBlocked("Host no esta en allowlist de salida.")

    host_is_ip = False
    try:
        ipaddress.ip_address(host)
        host_is_ip = True
    except ValueError:
        host_is_ip = False

    ips_to_check = {host} if host_is_ip else _resolve_host_ips(host)
    for ip in ips_to_check:
        if not _is_public_ip(ip):
            raise OutboundURLBlocked("IP interna o no publica bloqueada por SSRF.")

    return raw


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OutboundURLBlocked("Redirect externo bloqueado.")


def build_no_redirect_opener():
    return urllib.request.build_opener(_NoRedirectHandler())
