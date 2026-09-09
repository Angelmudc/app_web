from pathlib import Path

from app import app as flask_app
from public import routes as public_routes


def test_client_ai_plans_asset_get_is_public_jpeg_and_cached():
    response = flask_app.test_client().get("/public-assets/client-ai/planes-domestica")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.headers["Content-Type"].startswith("image/jpeg")
    assert response.headers["Cache-Control"] == "public, max-age=86400"


def test_client_ai_plans_asset_head_is_public_without_body():
    response = flask_app.test_client().head("/public-assets/client-ai/planes-domestica")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/jpeg")
    assert response.data == b""


def test_client_ai_plans_asset_returns_clean_404_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(public_routes, "CLIENT_AI_PLANS_ASSET_PATH", Path(tmp_path) / "missing.jpg")

    response = flask_app.test_client().get("/public-assets/client-ai/planes-domestica")

    assert response.status_code == 404


def test_client_ai_plans_asset_rejects_extra_path_and_traversal():
    client = flask_app.test_client()

    assert client.get("/public-assets/client-ai/planes-domestica/otro").status_code == 404
    assert client.get("/public-assets/client-ai/../planes_domestica.jpg").status_code == 404


def test_admin_chat_is_not_publicly_exposed():
    response = flask_app.test_client().get("/admin/chat")

    assert response.status_code in {302, 401, 403}
