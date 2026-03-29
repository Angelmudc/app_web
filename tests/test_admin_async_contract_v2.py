# -*- coding: utf-8 -*-

from utils.admin_async import payload


def test_payload_async_v2_defaults_compatibles():
    data = payload(
        success=True,
        message="ok",
        category="success",
        update_target="#regionA",
        replace_html="<div>ok</div>",
    )

    assert data.get("success") is True
    assert data.get("ok") is True
    assert data.get("update_target") == "#regionA"
    assert data.get("replace_html") == "<div>ok</div>"
    assert data.get("update_targets") == []
    assert data.get("invalidate_targets") == []


def test_payload_async_v2_acepta_targets_e_invalidaciones():
    update_targets = [
        {"target": "#regionA", "replace_html": "<div>A</div>"},
        {"target": "#regionB", "invalidate": True},
    ]
    invalidate_targets = ["#regionC"]

    data = payload(
        success=True,
        update_target="#regionA",
        update_targets=update_targets,
        invalidate_targets=invalidate_targets,
    )

    assert data.get("update_target") == "#regionA"
    assert data.get("update_targets") == update_targets
    assert data.get("invalidate_targets") == invalidate_targets
