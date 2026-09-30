from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_candidate_intake_uses_text_dom_apis_for_all_external_values():
    source = (ROOT / "static/js/admin_bot_candidate_intake.js").read_text(encoding="utf-8")

    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "outerHTML" not in source
    assert "textContent" in source
    assert "pre.textContent = JSON.stringify" in source
    assert "setAttribute('href'" in source


def test_nearby_sandbox_render_escapes_external_text_before_template_insertion():
    source = (ROOT / "static/js/admin_bot_sandbox_asistente.js").read_text(encoding="utf-8")

    assert "function esc(value)" in source
    assert "esc(item.inbound_text" in source
    assert "esc(m.text_body" in source
    assert "pretty(review.interview_collected_data)" in source
    assert "JSON.stringify(review.interview_collected_data" not in source
