from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_api_key(x_beacon_api_key: str = Header(...)) -> None:
    expected = get_settings().beacon_api_key
    if x_beacon_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Beacon API key",
        )
