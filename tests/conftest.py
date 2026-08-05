import os

import pytest


os.environ["BEACON_API_KEY"] = "test"
os.environ.setdefault(
    "NEXTCLOUD_CALDAV_URL", "https://example.invalid/remote.php/dav"
)
os.environ.setdefault("NEXTCLOUD_USERNAME", "test")
os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "test")
os.environ.setdefault("VIKUNJA_API_URL", "https://example.invalid/api/v1")
os.environ.setdefault("VIKUNJA_API_TOKEN", "test")
os.environ["BEACON_INTERPRETER"] = "rules"
os.environ["GEMINI_API_KEY"] = "test-not-a-real-gemini-key"
os.environ["CONVERSATION_ENABLED"] = "false"
os.environ.setdefault("CONTEXT_DATABASE_PATH", "/tmp/beacon-test-context.db")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
