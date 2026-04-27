from fastapi.testclient import TestClient

import amesh.gateway.app as gateway_app


def test_dashboard_spa_route_falls_back_to_index_for_browser_refresh(tmp_path, monkeypatch):
    dist = tmp_path / "ui" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('dashboard')", encoding="utf-8")

    monkeypatch.setattr(gateway_app, "_dashboard_dist_path", lambda: dist)

    client = TestClient(gateway_app.app)

    root_resp = client.get("/dashboard")
    refresh_resp = client.get("/dashboard/workspaces")
    nested_refresh_resp = client.get("/dashboard/projects/news-pulse")
    asset_resp = client.get("/dashboard/assets/app.js")

    assert root_resp.status_code == 200
    assert refresh_resp.status_code == 200
    assert nested_refresh_resp.status_code == 200
    assert "<div id=\"root\"></div>" in refresh_resp.text
    assert "<div id=\"root\"></div>" in nested_refresh_resp.text
    assert asset_resp.status_code == 200
    assert asset_resp.text == "console.log('dashboard')"


def test_dashboard_spa_fallback_keeps_asset_and_api_404s(tmp_path, monkeypatch):
    dist = tmp_path / "ui" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")

    monkeypatch.setattr(gateway_app, "_dashboard_dist_path", lambda: dist)

    client = TestClient(gateway_app.app)

    missing_asset_resp = client.get("/dashboard/assets/missing.js")
    api_resp = client.get("/management/v1/not-real")

    assert missing_asset_resp.status_code == 404
    assert missing_asset_resp.json()["detail"] == "Dashboard asset not found"
    assert api_resp.status_code == 404
    assert api_resp.json()["detail"] == "Not Found"
