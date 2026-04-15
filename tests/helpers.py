from fastapi.testclient import TestClient


def operator_headers(client: TestClient, secret: str = "unit-test-secret") -> dict[str, str]:
    response = client.post("/management/v1/auth/token", json={"operator_secret": secret})
    assert response.status_code == 200
    # The response is now enveloped in a 'data' field
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
