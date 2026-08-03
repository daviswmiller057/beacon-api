from hmac import compare_digest

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_api_key(x_beacon_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().beacon_api_key
    if x_beacon_api_key is None or not compare_digest(
        x_beacon_api_key.encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Beacon API key",
        )
