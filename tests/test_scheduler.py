from datetime import datetime, timezone

from scheduler import overlaps


def test_overlap():
    a = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
    b = datetime(2026, 8, 1, 11, tzinfo=timezone.utc)
    c = datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc)
    d = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    assert overlaps(a, b, c, d)
    assert not overlaps(a, b, b, d)


def test_common_timezones_are_available():
    from timezones import get_timezone

    assert get_timezone("Asia/Tokyo").key == "Asia/Tokyo"
    assert get_timezone("America/New_York").key == "America/New_York"
    assert get_timezone("Europe/London").key == "Europe/London"


def test_location_search_normalizes_and_filters_results():
    from location_search import _valid_unique_results

    assert _valid_unique_results(
        [
            {"name": "Tokyo", "admin1": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"},
            {"name": "Invalid", "admin1": "", "country": "Nowhere", "timezone": "Invalid/Zone"},
            {"name": "Tokyo", "admin1": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"},
        ]
    ) == [
        {"name": "Tokyo", "admin1": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"}
    ]


def test_location_search_skips_short_queries():
    from location_search import search_timezones

    assert search_timezones("A") == []


def test_location_search_supports_japanese_place_names(monkeypatch):
    import io
    import location_search

    payloads = iter(
        [
            io.BytesIO(
                """{"features":[{"properties":{"name":"東京都","country":"日本"},
                "geometry":{"type":"Point","coordinates":[139.7638947,35.6768601]}}]}""".encode()
            ),
            io.BytesIO(b'{"timezone":"Asia/Tokyo"}'),
        ]
    )
    monkeypatch.setattr(location_search, "urlopen", lambda request, timeout: next(payloads))

    assert location_search.search_timezones("東京") == [
        {"name": "東京都", "admin1": "", "country": "日本", "timezone": "Asia/Tokyo"}
    ]
