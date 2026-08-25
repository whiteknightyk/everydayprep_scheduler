from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from timezones import is_valid_timezone

PHOTON_URL = "https://photon.komoot.io/api/"
TIMEZONE_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "ja",
    "User-Agent": "SAT-Scheduler-MVP/0.1",
}


class LocationSearchError(RuntimeError):
    """Raised when a location search service cannot be reached or decoded."""


def _load_json(url: str) -> object:
    request = Request(url, headers=REQUEST_HEADERS)
    try:
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeError, OSError) as exc:
        raise LocationSearchError(
            "地名検索サービスに接続できませんでした。しばらくしてから再度お試しください。"
        ) from exc


def _valid_unique_results(items: list[dict[str, str]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        name = item.get("name", "")
        timezone = item.get("timezone", "")
        admin1 = item.get("admin1", "")
        country = item.get("country", "")
        if not name or not timezone or not is_valid_timezone(timezone):
            continue
        key = (name, admin1, country, timezone)
        if key in seen:
            continue
        seen.add(key)
        results.append({"name": name, "admin1": admin1, "country": country, "timezone": timezone})
    return results


def _search_photon(query: str, limit: int) -> list[dict[str, str]]:
    params = urlencode(
        [
            ("q", query),
            ("limit", str(limit)),
            ("layer", "city"),
            ("layer", "district"),
            ("layer", "locality"),
            ("layer", "county"),
            ("layer", "state"),
            ("layer", "country"),
        ]
    )
    payload = _load_json(f"{PHOTON_URL}?{params}")
    features = payload.get("features", []) if isinstance(payload, dict) else []
    if not isinstance(features, list):
        raise LocationSearchError("地名検索サービスから予期しない応答が返されました。")

    locations: list[dict[str, object]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        coordinates = geometry.get("coordinates")
        name = properties.get("name")
        if not isinstance(name, str) or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        longitude, latitude = coordinates[:2]
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            continue
        locations.append(
            {
                "name": name,
                "admin1": properties.get("state") if isinstance(properties.get("state"), str) else "",
                "country": properties.get("country") if isinstance(properties.get("country"), str) else "",
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    if not locations:
        return []

    timezone_params = urlencode(
        {
            "latitude": ",".join(str(item["latitude"]) for item in locations),
            "longitude": ",".join(str(item["longitude"]) for item in locations),
            "timezone": "auto",
            "forecast_days": 0,
        }
    )
    timezone_payload = _load_json(f"{TIMEZONE_URL}?{timezone_params}")
    timezone_rows = timezone_payload if isinstance(timezone_payload, list) else [timezone_payload]

    items: list[dict[str, str]] = []
    for location, timezone_row in zip(locations, timezone_rows):
        if not isinstance(timezone_row, dict) or not isinstance(timezone_row.get("timezone"), str):
            continue
        items.append(
            {
                "name": str(location["name"]),
                "admin1": str(location["admin1"]),
                "country": str(location["country"]),
                "timezone": timezone_row["timezone"],
            }
        )
    return _valid_unique_results(items)


def search_timezones(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Resolve a place name to a compact list of IANA timezone candidates."""
    query = query.strip()
    if len(query) < 2:
        return []

    limit = max(1, min(limit, 10))
    return _search_photon(query, limit)
