"""Daily custom ranges are bounded, inclusive, and do not fetch invalid windows."""
from datetime import date
from unittest.mock import patch
from app.wsgi import app
from features.cso_history import page as P


def render(query):
    with app.test_request_context(), patch.object(P.sb, "is_configured", return_value=True), patch.object(P, "_fetch_daily_colors", return_value=[]) as fetch:
        status, _, body = P.handle_daily_colors(query, b"")
    return status, body.decode(), fetch


def test_custom_range_includes_both_dates_across_dst():
    status, html, fetch = render({"start": ["2026-10-31"], "end": ["2026-11-02"]})
    assert status == 200
    fetch.assert_called_once_with(date(2026, 10, 31), date(2026, 11, 2))
    assert 'value="2026-10-31"' in html and 'value="2026-11-02"' in html


def test_invalid_or_unbounded_dates_fall_back_with_an_explanation():
    for query in ({"start": ["bad"], "end": ["2026-11-02"]},
                  {"start": ["2026-11-02"], "end": ["2026-10-31"]},
                  {"start": ["2024-01-01"], "end": ["2026-11-02"]},
                  {"start": ["2999-01-01"], "end": ["2999-01-02"]}):
        status, html, fetch = render(query)
        assert status == 200 and 'role="alert"' in html
        first, last = fetch.call_args.args
        assert (last-first).days == 29


def test_sources_available_even_when_history_is_unavailable():
    with app.test_request_context(), patch.object(P.sb, "is_configured", return_value=False):
        for handler in (P.handle_page, P.handle_daily_colors):
            status, _, body = handler({}, b"")
            assert status == 200
            html = body.decode()
            for endpoint in ("getBeaches", "getCSV", "/cso-history/api/events"):
                assert endpoint in html
            assert 'A posting change alone does not establish a new sample.' in html
