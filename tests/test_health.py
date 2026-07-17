import os

os.environ.setdefault("BEACON_API_KEY", "test")
os.environ.setdefault("NEXTCLOUD_CALDAV_URL", "https://example.invalid/remote.php/dav")
os.environ.setdefault("NEXTCLOUD_USERNAME", "test")
os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "test")

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
