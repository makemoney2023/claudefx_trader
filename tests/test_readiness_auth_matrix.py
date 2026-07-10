"""
Wave 2 Task 6 — auth matrix for mutating API routes.
"""

import pytest
from fastapi.testclient import TestClient


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("BOT_API_KEY", "matrix-test-key")
    import trading_bot.api.auth as auth_module

    auth_module._API_KEY = None
    key = auth_module.get_api_key()
    from trading_bot.api.main import app

    return TestClient(app), key


def _collect_mutating_routes(app):
    routes = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in methods:
            if method in MUTATING_METHODS:
                routes.append((method, path))
    return sorted(set(routes))


class TestAuthMatrix:
    def test_status_and_health_are_public(self, api_client):
        client, _ = api_client
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/bot/status").status_code == 200

    def test_all_mutating_routes_require_auth(self, api_client):
        client, key = api_client
        from trading_bot.api.main import app

        failures = []
        for method, path in _collect_mutating_routes(app):
            if path.startswith("/ws"):
                continue
            response = client.request(method, path, json={})
            if response.status_code not in (401, 403, 404, 405, 422):
                failures.append((method, path, response.status_code))

        assert failures == [], f"Unprotected mutating routes: {failures}"

    def test_mutating_routes_accept_valid_key(self, api_client):
        client, key = api_client
        response = client.post(
            "/api/bot/stop",
            headers={"X-API-Key": key},
        )
        assert response.status_code in (200, 400, 500)

    def test_config_trading_put_requires_auth(self, api_client):
        client, key = api_client
        payload = {"risk_per_trade": 0.02}
        assert client.put("/api/config/trading", json=payload).status_code == 401
        assert (
            client.put(
                "/api/config/trading",
                json=payload,
                headers={"X-API-Key": key},
            ).status_code
            in (200, 403)
        )
