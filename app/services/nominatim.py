import threading
import time
from typing import Any, ClassVar, Protocol

import httpx

from app.models import LocationCandidate
from app.services.location import LocationProviderError


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class NominatimLocationProvider:
    """Nominatim-compatible place search isolated from Beacon domain logic."""

    name = "nominatim"
    attribution = "© OpenStreetMap contributors"
    _PUBLIC_URL = "https://nominatim.openstreetmap.org"
    _PUBLIC_INTERVAL_SECONDS = 1.0
    _CACHE_LIMIT = 256
    _cache: ClassVar[dict[tuple[str, str], list[LocationCandidate]]] = {}
    _last_public_request: ClassVar[float] = 0.0
    _state_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        user_agent: str,
        client: HttpClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.client = client or httpx

    def search(
        self,
        query: str,
        *,
        geographic_bias: str | None = None,
    ) -> list[LocationCandidate]:
        search_text = query
        if geographic_bias and geographic_bias.casefold() not in query.casefold():
            search_text = f"{query}, {geographic_bias}"
        cache_key = (self.base_url.casefold(), search_text.casefold())
        with self._state_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [candidate.model_copy(deep=True) for candidate in cached]

        self._throttle_public_endpoint()
        try:
            response = self.client.get(
                f"{self.base_url}/search",
                params={
                    "q": search_text,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "namedetails": 1,
                    "limit": 5,
                    "dedupe": 1,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LocationProviderError("location lookup timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LocationProviderError(
                f"location provider returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise LocationProviderError("location provider could not be reached") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise LocationProviderError("location provider returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise LocationProviderError("location provider response was not a list")
        candidates: list[LocationCandidate] = []
        for item in payload:
            try:
                candidate = self._candidate(item)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None:
                candidates.append(candidate)
        with self._state_lock:
            if len(self._cache) >= self._CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = [
                candidate.model_copy(deep=True) for candidate in candidates
            ]
        return candidates

    def _throttle_public_endpoint(self) -> None:
        if self.base_url.casefold() != self._PUBLIC_URL:
            return
        with self._state_lock:
            elapsed = time.monotonic() - self._last_public_request
            wait_seconds = self._PUBLIC_INTERVAL_SECONDS - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            type(self)._last_public_request = time.monotonic()

    def _candidate(self, item: Any) -> LocationCandidate | None:
        if not isinstance(item, dict):
            return None
        display_name = str(item.get("display_name") or "").strip()
        raw_namedetails = item.get("namedetails") or {}
        raw_address = item.get("address") or {}
        namedetails = raw_namedetails if isinstance(raw_namedetails, dict) else {}
        address = raw_address if isinstance(raw_address, dict) else {}
        canonical_name = str(
            item.get("name")
            or namedetails.get("name")
            or self._first_place_name(address)
            or display_name.split(",", 1)[0]
        ).strip()
        if not canonical_name:
            return None
        return LocationCandidate(
            canonical_name=canonical_name,
            formatted_address=self._formatted_address(
                address,
                display_name,
                canonical_name,
            ),
            latitude=self._optional_float(item.get("lat")),
            longitude=self._optional_float(item.get("lon")),
            provider_id=(
                f"{item.get('osm_type')}:{item.get('osm_id')}"
                if item.get("osm_type") and item.get("osm_id")
                else str(item.get("place_id")) if item.get("place_id") else None
            ),
            place_type=str(item.get("type") or item.get("class") or "") or None,
        )

    @staticmethod
    def _first_place_name(address: dict[str, Any]) -> str | None:
        for key in (
            "amenity",
            "theatre",
            "arts_centre",
            "building",
            "office",
            "tourism",
            "shop",
            "university",
            "school",
        ):
            if address.get(key):
                return str(address[key])
        return None

    @classmethod
    def _formatted_address(
        cls,
        address: dict[str, Any],
        display_name: str,
        canonical_name: str,
    ) -> str | None:
        street = " ".join(
            str(value).strip()
            for value in (address.get("house_number"), address.get("road"))
            if value
        )
        city = next(
            (
                str(address[key]).strip()
                for key in ("city", "town", "village", "municipality", "county")
                if address.get(key)
            ),
            "",
        )
        state = str(address.get("state_code") or "").strip()
        if not state:
            iso_state = str(address.get("ISO3166-2-lvl4") or "")
            state = iso_state.rsplit("-", 1)[-1] if "-" in iso_state else ""
        if not state:
            state = str(address.get("state") or "").strip()
        postcode = str(address.get("postcode") or "").strip()
        state_postcode = " ".join(value for value in (state, postcode) if value)
        parts = [value for value in (street, city, state_postcode) if value]
        if parts:
            return ", ".join(dict.fromkeys(parts))

        display_parts = [part.strip() for part in display_name.split(",") if part.strip()]
        if display_parts and cls._normalize(display_parts[0]) == cls._normalize(canonical_name):
            display_parts = display_parts[1:]
        return ", ".join(display_parts) or None

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().replace(".", "").split())

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
