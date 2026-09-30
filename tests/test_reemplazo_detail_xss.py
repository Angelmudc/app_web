from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates/admin/reemplazo_detail.html"


def _search_script(source: str) -> str:
    start = source.index("function renderRows(items)")
    end = source.index("function doSearch()", start)
    return source[start:end]


def test_reemplazo_candidate_results_use_text_nodes_and_controlled_id_attribute():
    source = TEMPLATE.read_text(encoding="utf-8")
    render_rows = _search_script(source)

    assert "box.innerHTML" not in render_rows
    assert "insertAdjacentHTML" not in render_rows
    assert "outerHTML" not in render_rows
    assert "document.createElement" in render_rows
    assert "textContent" in render_rows
    assert "setAttribute('data-candidata-id'" in render_rows


def test_reemplazo_xss_payloads_are_not_used_as_html_sinks():
    source = TEMPLATE.read_text(encoding="utf-8")
    render_rows = _search_script(source)
    payloads = (
        '<img src=x onerror=alert(1)>',
        '<svg onload=alert(1)>',
        '<script>alert(1)</script>',
        '"><img src=x onerror=alert(1)>',
    )

    for payload in payloads:
        assert payload not in render_rows
    assert "item.nombre" in render_rows
    assert "item.codigo" in render_rows
    assert "item.ciudad" in render_rows
    assert "item.modalidad" in render_rows
    assert "item.telefono_masked" in render_rows
    assert "item.estado" in render_rows
