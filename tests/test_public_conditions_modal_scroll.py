from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static/js/clientes/public_conditions_modal.js"

HTML = """
<style>
  body { margin: 0; }
  #spacer { height: 4000px; }
  .employment-conditions-modal { display: none; }
  .employment-conditions-modal.public-conditions-open { display: grid; }
  .employment-conditions-body { height: 180px; overflow: auto; }
</style>
<div id="spacer"></div>
<form id="publicSolicitudForm" data-conditions-modal-id="publicEmploymentConditionsModal">
  <button type="submit" id="submit">Enviar solicitud</button>
</form>
<div id="publicEmploymentConditionsModal" class="employment-conditions-modal" aria-hidden="true">
  <div class="employment-conditions-dialog">
    <div class="employment-conditions-body" tabindex="0" role="region">
      <div style="height: 700px">Condiciones</div>
    </div>
    <button type="button" class="employment-conditions-read-more">Ver más</button>
    <button type="button" class="employment-conditions-confirm">Confirmo</button>
    <button type="button" data-public-conditions-dismiss id="close">Volver al formulario</button>
  </div>
</div>
"""


def test_modal_source_is_scroll_neutral_and_has_one_lifecycle():
    source = SOURCE.read_text()
    forbidden = (
        "scrollPosition",
        "restoreBackground",
        "lockBackground",
        "window.scrollTo",
        "window.scrollBy",
        "document.body.style.position",
        "document.body.style.top",
        "public-conditions-fallback",
        "bootstrap.Modal",
    )
    for token in forbidden:
        assert token not in source
    assert "focus({ preventScroll: true })" in source
    assert "passive: false" in source
    assert "body.contains(event.target)" in source
    assert "removeActiveListeners" in source


def test_modal_open_close_blocks_backdrop_without_touching_document_scroll():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment-specific
            pytest.skip(f"Chromium no disponible: {exc}")

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_content(HTML)
        page.add_script_tag(path=str(SOURCE))
        page.evaluate(
            """
            () => {
              window.__scrollCalls = 0;
              window.__focusOptions = [];
              window.scrollTo = () => { window.__scrollCalls += 1; };
              const originalFocus = HTMLElement.prototype.focus;
              HTMLElement.prototype.focus = function (options) {
                window.__focusOptions.push({ id: this.id, preventScroll: !!(options && options.preventScroll) });
                return originalFocus.call(this, options);
              };
              const form = document.querySelector('form');
              window.__controller = window.PublicConditionsModal.setup(form);
              form.addEventListener('submit', event => {
                event.preventDefault();
                window.__controller.open();
              });
            }
            """
        )
        before = page.evaluate("() => window.scrollY")
        page.locator("#submit").click()
        opened = page.evaluate(
            """
            () => {
              const modal = document.querySelector('.employment-conditions-modal');
              const backdropWheel = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 100 });
              modal.dispatchEvent(backdropWheel);
              const body = document.querySelector('.employment-conditions-body');
              const bodyWheel = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 100 });
              body.dispatchEvent(bodyWheel);
              const backdropTouch = new Event('touchmove', { bubbles: true, cancelable: true });
              modal.dispatchEvent(backdropTouch);
              const key = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'PageDown' });
              modal.dispatchEvent(key);
              return {
                scroll: window.scrollY,
                bodyPosition: document.body.style.position,
                bodyTop: document.body.style.top,
                htmlOverflow: document.documentElement.style.overflow,
                active: document.activeElement.className,
                wheelBackdropBlocked: backdropWheel.defaultPrevented,
                wheelBodyAllowed: bodyWheel.defaultPrevented,
                touchBackdropBlocked: backdropTouch.defaultPrevented,
                keyBackdropBlocked: key.defaultPrevented,
                scrollCalls: window.__scrollCalls,
                focusOptions: window.__focusOptions,
              };
            }
            """
        )
        assert abs(opened["scroll"] - before) <= 2
        assert opened["bodyPosition"] == ""
        assert opened["bodyTop"] == ""
        assert opened["htmlOverflow"] == ""
        assert opened["wheelBackdropBlocked"] is True
        assert opened["wheelBodyAllowed"] is False
        assert opened["touchBackdropBlocked"] is True
        assert opened["keyBackdropBlocked"] is True
        assert opened["scrollCalls"] == 0
        assert opened["focusOptions"][-1]["preventScroll"] is True

        page.locator("#close").click()
        closed = page.evaluate(
            """
            () => {
              const modal = document.querySelector('.employment-conditions-modal');
              const afterClose = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 100 });
              modal.dispatchEvent(afterClose);
              return {
                scroll: window.scrollY,
                bodyPosition: document.body.style.position,
                bodyTop: document.body.style.top,
                htmlOverflow: document.documentElement.style.overflow,
                active: document.activeElement.id,
                scrollCalls: window.__scrollCalls,
                afterCloseBlocked: afterClose.defaultPrevented,
              };
            }
            """
        )
        assert abs(closed["scroll"] - before) <= 2
        assert closed["bodyPosition"] == ""
        assert closed["bodyTop"] == ""
        assert closed["htmlOverflow"] == ""
        assert closed["active"] == "submit"
        assert closed["scrollCalls"] == 0
        assert closed["afterCloseBlocked"] is False

        for _ in range(5):
            page.locator("#submit").click()
            page.locator("#close").click()
        final = page.evaluate("() => ({scroll: window.scrollY, active: document.activeElement.id, scrollCalls: window.__scrollCalls})")
        assert abs(final["scroll"] - before) <= 2
        assert final["active"] == "submit"
        assert final["scrollCalls"] == 0
        browser.close()
