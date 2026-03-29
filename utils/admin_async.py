from __future__ import annotations


def wants_json(request) -> bool:
    try:
        accept = (request.headers.get("Accept") or "").lower()
        xrw = (request.headers.get("X-Requested-With") or "").lower()
        async_hdr = (request.headers.get("X-Admin-Async") or "").lower()
        return bool(
            request.is_json
            or ("application/json" in accept)
            or (xrw == "xmlhttprequest")
            or (async_hdr in ("1", "true", "yes"))
        )
    except Exception:
        return False


def payload(
    *,
    success: bool,
    message: str = "",
    category: str = "info",
    redirect_url: str | None = None,
    replace_html: str | None = None,
    update_target: str | None = None,
    update_targets: list | None = None,
    invalidate_targets: list | None = None,
    remove_element: str | None = None,
    errors: list | None = None,
    error_code: str | None = None,
    extra: dict | None = None,
) -> dict:
    data = {
        "success": bool(success),
        "ok": bool(success),  # compatibilidad
        "message": message or "",
        "category": (category or "info"),
        "redirect_url": redirect_url,
        "replace_html": replace_html,
        "update_target": update_target,
        "update_targets": update_targets or [],
        "invalidate_targets": invalidate_targets or [],
        "remove_element": remove_element,
        "errors": errors or [],
        "error_code": error_code,
    }
    if extra:
        data.update(extra)
    return data
