import logging
import re
from typing import Protocol

from app.config import Settings
from app.models import (
    LocationCandidate,
    LocationResolution,
    LocationResolutionStatus,
)


logger = logging.getLogger("beacon.location")


class LocationProviderError(RuntimeError):
    """An external place provider could not complete a lookup."""


class LocationLookupProvider(Protocol):
    name: str
    attribution: str | None

    def search(
        self,
        query: str,
        *,
        geographic_bias: str | None = None,
    ) -> list[LocationCandidate]: ...


class LocationResolver(Protocol):
    def resolve(
        self,
        query: str,
        *,
        geographic_bias: str | None = None,
    ) -> LocationResolution: ...


class DeterministicLocationResolver:
    """Rank provider-neutral candidates without model judgment."""

    _PLACE_TYPES = {
        "amenity",
        "arts_centre",
        "building",
        "business",
        "church",
        "college",
        "commercial",
        "hotel",
        "music_venue",
        "office",
        "opera_house",
        "place_of_worship",
        "school",
        "theatre",
        "university",
        "venue",
    }
    _HIGH_CONFIDENCE = 0.72
    _MINIMUM_LEAD = 0.12

    def __init__(self, provider: LocationLookupProvider) -> None:
        self.provider = provider

    def resolve(
        self,
        query: str,
        *,
        geographic_bias: str | None = None,
    ) -> LocationResolution:
        cleaned_query = " ".join(query.split())
        try:
            candidates = self.provider.search(
                cleaned_query,
                geographic_bias=geographic_bias,
            )
        except LocationProviderError as exc:
            logger.warning(
                "Location provider %s was unavailable: %s",
                self.provider.name,
                exc,
            )
            return LocationResolution(
                query=cleaned_query,
                status=LocationResolutionStatus.UNAVAILABLE,
                provider=self.provider.name,
                attribution=self.provider.attribution,
                detail=str(exc),
            )

        if not candidates:
            return LocationResolution(
                query=cleaned_query,
                status=LocationResolutionStatus.NOT_FOUND,
                provider=self.provider.name,
                attribution=self.provider.attribution,
            )

        unique_candidates: dict[tuple[str, str], LocationCandidate] = {}
        for candidate in candidates:
            key = (
                self._normalize(candidate.canonical_name),
                self._normalize(candidate.formatted_address or ""),
            )
            unique_candidates.setdefault(key, candidate)
        ranked = sorted(
            (
                self._score(candidate, cleaned_query, geographic_bias)
                for candidate in unique_candidates.values()
            ),
            key=lambda item: (
                -item.confidence,
                self._normalize(item.canonical_name),
                self._normalize(item.formatted_address or ""),
                item.provider_id or "",
            ),
        )
        top = ranked[0]
        lead = top.confidence - ranked[1].confidence if len(ranked) > 1 else 1
        if top.confidence >= self._HIGH_CONFIDENCE and lead >= self._MINIMUM_LEAD:
            return LocationResolution(
                query=cleaned_query,
                status=LocationResolutionStatus.RESOLVED,
                provider=self.provider.name,
                attribution=self.provider.attribution,
                selected=top,
                alternatives=ranked[1:3],
            )
        if len(ranked) > 1 and top.confidence >= self._HIGH_CONFIDENCE:
            return LocationResolution(
                query=cleaned_query,
                status=LocationResolutionStatus.AMBIGUOUS,
                provider=self.provider.name,
                attribution=self.provider.attribution,
                alternatives=ranked[:3],
            )
        return LocationResolution(
            query=cleaned_query,
            status=LocationResolutionStatus.NOT_FOUND,
            provider=self.provider.name,
            attribution=self.provider.attribution,
            alternatives=ranked[:3],
        )

    def _score(
        self,
        candidate: LocationCandidate,
        query: str,
        geographic_bias: str | None,
    ) -> LocationCandidate:
        query_normalized = self._normalize(query)
        name_normalized = self._normalize(candidate.canonical_name)
        display_normalized = self._normalize(
            " ".join(
                value
                for value in (
                    candidate.canonical_name,
                    candidate.formatted_address,
                )
                if value
            )
        )
        evidence: list[str] = []
        score = 0.0

        if name_normalized == query_normalized:
            score += 0.65
            evidence.append("exact_name")
        elif f" {query_normalized} " in f" {name_normalized} ":
            score += 0.55
            evidence.append("name_contains_query")

        query_tokens = set(query_normalized.split())
        display_tokens = set(display_normalized.split())
        if query_tokens:
            overlap = len(query_tokens & display_tokens) / len(query_tokens)
            score += overlap * 0.2
            if overlap:
                evidence.append(f"token_overlap:{overlap:.2f}")

        if self._normalize(candidate.place_type or "") in self._PLACE_TYPES:
            score += 0.1
            evidence.append("venue_classification")

        bias_normalized = self._normalize(geographic_bias or "")
        bias_tokens = set(bias_normalized.split())
        address_tokens = set(self._normalize(candidate.formatted_address or "").split())
        if bias_tokens:
            bias_overlap = len(bias_tokens & address_tokens) / len(bias_tokens)
            score += bias_overlap * 0.15
            if bias_overlap:
                evidence.append(f"geographic_bias:{bias_overlap:.2f}")

        return candidate.model_copy(
            update={
                "confidence": min(round(score, 4), 1.0),
                "matching_evidence": evidence,
            }
        )

    @staticmethod
    def _normalize(value: str) -> str:
        value = value.casefold().replace(".", "").replace("'", "").replace("’", "")
        return " ".join(re.sub(r"[^\w]+", " ", value).split())


def build_location_resolver(settings: Settings) -> LocationResolver | None:
    if not settings.beacon_location_lookup_enabled:
        return None
    if settings.beacon_location_provider == "nominatim":
        from app.services.nominatim import NominatimLocationProvider

        return DeterministicLocationResolver(
            NominatimLocationProvider(
                base_url=settings.beacon_location_api_url,
                timeout_seconds=settings.beacon_location_timeout_seconds,
                user_agent=settings.beacon_location_user_agent,
            )
        )
    raise ValueError(
        f"Unsupported BEACON_LOCATION_PROVIDER: {settings.beacon_location_provider}"
    )
