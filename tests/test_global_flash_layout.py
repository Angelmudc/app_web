from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_global_flash_host_is_outside_main_and_has_safe_positioning():
    template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "base.css").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")

    host = template.index('<div class="app-flash-container"')
    main = template.index('<main class="container">')
    assert host < main
    assert ".app-flash-container" in css
    assert "position: fixed" in css
    assert "top: 4.5rem" in css

    toast_rule_start = styles.index(".toast{")
    toast_rule_end = styles.index("}", toast_rule_start)
    toast_rule = styles[toast_rule_start:toast_rule_end]
    assert "position: fixed" not in toast_rule
    assert "bottom:" not in toast_rule

