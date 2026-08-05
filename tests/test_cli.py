"""Tests for the dremio-client CLI."""

import base64
import datetime as dt
import json
from typing import Any
from unittest import mock

import click
import pytest
from click.testing import CliRunner

from dremio_client.cli import (
    COOKIE_NAME,
    _json_or_die,
    authenticate,
    check_written,
    cookie_expiry,
    fetch_rows,
    main,
    quote_path,
)


def make_jwt(days_until_expiry: float) -> str:
    """A JWT shaped like the Cloudflare Access cookie. The signature is not checked."""
    exp = dt.datetime.now(dt.UTC) + dt.timedelta(days=days_until_expiry)
    payload = {"exp": int(exp.timestamp()), "iat": 0, "email": "someone@example.org"}

    def seg(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.{'x' * 40}"


def fake_response(status: int, content_type: str, body: str) -> mock.Mock:
    r = mock.Mock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.text = body
    r.json = lambda: json.loads(body)
    return r


# ---------------------------------------------------------------- help smoke tests


def test_cli_help() -> None:
    """Test that CLI help works."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "JGI Dremio lakehouse CLI" in result.output


def test_cli_login_help() -> None:
    """Test that login subcommand help works."""
    runner = CliRunner()
    result = runner.invoke(main, ["login", "--help"])
    assert result.exit_code == 0
    assert "Test authentication" in result.output


def test_cli_query_help() -> None:
    """Test that query subcommand help works."""
    runner = CliRunner()
    result = runner.invoke(main, ["query", "--help"])
    assert result.exit_code == 0
    assert "Run a SQL query" in result.output


def test_cli_export_help() -> None:
    """Test that export subcommand help works."""
    runner = CliRunner()
    result = runner.invoke(main, ["export", "--help"])
    assert result.exit_code == 0
    assert "handles pagination" in result.output


# ---------------------------------------------------------------- cookie expiry


def test_cookie_expiry_reads_exp() -> None:
    exp = cookie_expiry(make_jwt(10))
    assert exp is not None
    days = (exp - dt.datetime.now(dt.UTC)).total_seconds() / 86400
    assert 9.9 < days < 10.1


def test_cookie_expiry_handles_expired() -> None:
    exp = cookie_expiry(make_jwt(-2))
    assert exp is not None
    assert exp < dt.datetime.now(dt.UTC)


@pytest.mark.parametrize("bad", ["not-a-jwt", "a.b", "a.!!!.c", ""])
def test_cookie_expiry_returns_none_for_non_jwt(bad: str) -> None:
    assert cookie_expiry(bad) is None


# ---------------------------------------------------------------- Cloudflare block


def test_html_response_names_cloudflare_not_json() -> None:
    """Cloudflare answers a cookie-less request with 200 plus HTML, not a 401."""
    r = fake_response(200, "text/html", "<html>sign in</html>")
    with pytest.raises(click.ClickException) as e:
        _json_or_die(r, "Dremio login")
    assert "Cloudflare" in str(e.value)
    assert COOKIE_NAME in str(e.value)


def test_rejected_password_is_reported_as_a_dremio_failure() -> None:
    body = json.dumps({"errorMessage": "Login failed: Invalid username or password"})
    with (
        mock.patch("requests.post", return_value=fake_response(401, "application/json", body)),
        pytest.raises(click.ClickException) as e,
    ):
        authenticate("https://x", "u", "p", make_jwt(10), True)
    assert "Dremio rejected the login" in str(e.value)


# ---------------------------------------------------------------- the cookie regression


def test_login_sends_the_cloudflare_cookie() -> None:
    """login used to omit the cookie that query and export both sent, so it could
    never reach Dremio and always died parsing Cloudflare's HTML."""
    token = make_jwt(10)
    captured: dict[str, Any] = {}

    def capture(*_args: Any, **kw: Any) -> mock.Mock:
        captured.update(kw)
        return fake_response(200, "application/json", json.dumps({"token": "session-token-value"}))

    with mock.patch("requests.post", side_effect=capture):
        result = CliRunner().invoke(main, ["login", "--user", "u", "--password", "p", "--cf-token", token])

    assert result.exit_code == 0, result.output
    assert captured["cookies"] == {COOKIE_NAME: token}
    assert "passed" in result.output


def test_login_does_not_print_the_token_by_default() -> None:
    with mock.patch(
        "requests.post",
        return_value=fake_response(200, "application/json", json.dumps({"token": "session-token-value"})),
    ):
        result = CliRunner().invoke(main, ["login", "--user", "u", "--password", "p", "--cf-token", make_jwt(10)])
    assert result.exit_code == 0, result.output
    assert "session-token-value" not in result.output

    with mock.patch(
        "requests.post",
        return_value=fake_response(200, "application/json", json.dumps({"token": "session-token-value"})),
    ):
        shown = CliRunner().invoke(
            main, ["login", "--user", "u", "--password", "p", "--cf-token", make_jwt(10), "--show-token"]
        )
    assert "session-token-value" in shown.output


# ---------------------------------------------------------------- TLS


def test_pagination_follows_every_page() -> None:
    """A single unpaginated fetch returns only Dremio's first page."""
    all_rows = [{"n": i} for i in range(1250)]

    def paged(*_args: Any, **kw: Any) -> mock.Mock:
        p = kw["params"]
        window = all_rows[p["offset"] : p["offset"] + p["limit"]]
        return fake_response(200, "application/json", json.dumps({"rowCount": len(all_rows), "rows": window}))

    with mock.patch("requests.get", side_effect=paged):
        got = list(fetch_rows("https://x", "job", len(all_rows), {}, {}, True))
    assert got == all_rows


def test_pagination_stops_at_stop_after() -> None:
    all_rows = [{"n": i} for i in range(1000)]

    def paged(*_args: Any, **kw: Any) -> mock.Mock:
        p = kw["params"]
        window = all_rows[p["offset"] : p["offset"] + p["limit"]]
        return fake_response(200, "application/json", json.dumps({"rowCount": len(all_rows), "rows": window}))

    with mock.patch("requests.get", side_effect=paged):
        got = list(fetch_rows("https://x", "job", len(all_rows), {}, {}, True, stop_after=7))
    assert got == all_rows[:7]


def test_rows_are_returned_even_when_rowcount_lies() -> None:
    """DESCRIBE TABLE reports rowCount 0 while returning rows.

    Measured against the live lakehouse 2026-08-05: 13 rows, rowCount 0. A loop that
    trusts rowCount as its bound yields nothing and the schema dump silently loses
    the table.
    """
    described = [{"COLUMN_NAME": f"c{i}"} for i in range(13)]
    calls = {"n": 0}

    def one_page_then_empty(*_args: Any, **_kw: Any) -> mock.Mock:
        calls["n"] += 1
        rows = described if calls["n"] == 1 else []
        return fake_response(200, "application/json", json.dumps({"rowCount": 0, "rows": rows}))

    with mock.patch("requests.get", side_effect=one_page_then_empty):
        got = list(fetch_rows("https://x", "job", 0, {}, {}, True))
    assert got == described


def test_check_written_tolerates_the_rowcount_zero_case() -> None:
    check_written(13, 0)  # DESCRIBE: rowCount 0, rows real. Must not raise.
    check_written(500, 500)
    with pytest.raises(click.ClickException):
        check_written(400, 500)


def test_quote_path_quotes_each_part_of_a_spacey_source() -> None:
    assert quote_path("gold-db-2 postgresql.gold", "study") == '"gold-db-2 postgresql"."gold"."study"'


def test_tls_verification_is_on_by_default_and_off_only_when_asked() -> None:
    body = json.dumps({"token": "t"})
    for argv, expected in (([], True), (["--insecure"], False)):
        with mock.patch("requests.post", return_value=fake_response(200, "application/json", body)) as p:
            result = CliRunner().invoke(
                main, [*argv, "login", "--user", "u", "--password", "p", "--cf-token", make_jwt(9)]
            )
        assert result.exit_code == 0, result.output
        assert p.call_args.kwargs["verify"] is expected
