import httpx
import pytest

from app.models import LocationResolutionStatus
from app.services.location import DeterministicLocationResolver
from app.services.nominatim import NominatimLocationProvider


class FakeHttpClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def response(payload, status=200):
    return httpx.Response(
        status,
        request=httpx.Request("GET", "https://geo.example/search"),
        json=payload,
    )


def provider(client):
    return NominatimLocationProvider(
        base_url="https://geo.example/",
        timeout_seconds=7.5,
        user_agent="Beacon tests/contact@example.invalid",
        client=client,
    )


def ad_players_result():
    return {
        "place_id": 123,
        "osm_type": "way",
        "osm_id": 456,
        "name": "A.D. Players at the George Theater",
        "display_name": (
            "A.D. Players at the George Theater, 5420 Westheimer Road, "
            "Houston, Texas, 77056, United States"
        ),
        "lat": "29.739",
        "lon": "-95.472",
        "class": "amenity",
        "type": "theatre",
        "address": {
            "house_number": "5420",
            "road": "Westheimer Road",
            "city": "Houston",
            "state_code": "TX",
            "postcode": "77056",
        },
        "namedetails": {"name": "A.D. Players at the George Theater"},
    }


def test_nominatim_high_confidence_result_is_normalized_and_selected():
    client = FakeHttpClient(response([ad_players_result()]))
    resolver = DeterministicLocationResolver(provider(client))

    result = resolver.resolve("AD Players", geographic_bias="Houston, TX")

    assert result.status is LocationResolutionStatus.RESOLVED
    assert result.selected.canonical_name == "A.D. Players at the George Theater"
    assert result.selected.formatted_address == (
        "5420 Westheimer Road, Houston, TX 77056"
    )
    assert result.selected.latitude == 29.739
    assert result.selected.longitude == -95.472
    assert result.selected.provider_id == "way:456"
    assert result.selected.confidence >= 0.72
    assert result.attribution == "© OpenStreetMap contributors"
    url, kwargs = client.calls[0]
    assert url == "https://geo.example/search"
    assert kwargs["params"]["q"] == "AD Players, Houston, TX"
    assert kwargs["headers"]["User-Agent"] == (
        "Beacon tests/contact@example.invalid"
    )
    assert kwargs["timeout"] == 7.5

    repeated = resolver.resolve("AD Players", geographic_bias="Houston, TX")
    assert repeated.selected.provider_id == "way:456"
    assert len(client.calls) == 1


def test_deterministic_ranking_rejects_candidates_without_a_clear_lead():
    first = ad_players_result() | {
        "osm_id": 1,
        "name": "St. Luke's Church",
        "type": "place_of_worship",
        "address": {
            "house_number": "3471",
            "road": "Westheimer Road",
            "city": "Houston",
            "state_code": "TX",
        },
    }
    second = ad_players_result() | {
        "osm_id": 2,
        "name": "St. Luke's Chapel",
        "type": "place_of_worship",
        "address": {
            "house_number": "11011",
            "road": "Hall Road",
            "city": "Houston",
            "state_code": "TX",
        },
    }
    client = FakeHttpClient(response([first, second]))

    result = DeterministicLocationResolver(provider(client)).resolve(
        "St. Luke's",
        geographic_bias="Houston, TX",
    )

    assert result.status is LocationResolutionStatus.AMBIGUOUS
    assert len(result.alternatives) == 2
    assert result.selected is None


def test_nominatim_no_match_is_typed_not_found():
    result = DeterministicLocationResolver(
        provider(FakeHttpClient(response([])))
    ).resolve("Obscure Venue", geographic_bias="Houston, TX")

    assert result.status is LocationResolutionStatus.NOT_FOUND


def test_single_weak_candidate_is_not_treated_as_ambiguity():
    weak = ad_players_result() | {
        "name": "Completely Different Business",
        "type": "house",
        "address": {"city": "Dallas", "state_code": "TX"},
    }
    result = DeterministicLocationResolver(
        provider(FakeHttpClient(response([weak])))
    ).resolve("Unknown Hall", geographic_bias="Houston, TX")

    assert result.status is LocationResolutionStatus.NOT_FOUND
    assert len(result.alternatives) == 1


def test_nominatim_timeout_is_typed_unavailable_without_raw_payload():
    request = httpx.Request("GET", "https://geo.example/search")
    client = FakeHttpClient(error=httpx.ReadTimeout("slow", request=request))

    result = DeterministicLocationResolver(provider(client)).resolve(
        "Private Event Venue",
        geographic_bias="Houston, TX",
    )

    assert result.status is LocationResolutionStatus.UNAVAILABLE
    assert result.detail == "location lookup timed out"


def test_public_nominatim_requests_are_throttled(monkeypatch):
    public_provider = NominatimLocationProvider(
        base_url="https://nominatim.openstreetmap.org",
        timeout_seconds=10,
        user_agent="Beacon tests/contact@example.invalid",
        client=FakeHttpClient(response([])),
    )
    NominatimLocationProvider._cache.clear()
    NominatimLocationProvider._last_public_request = 100.0
    clock = iter([100.25, 101.0])
    sleeps = []
    monkeypatch.setattr(
        "app.services.nominatim.time.monotonic",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        "app.services.nominatim.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    public_provider.search("Unique policy test", geographic_bias="Houston, TX")

    assert sleeps == [pytest.approx(0.75)]
