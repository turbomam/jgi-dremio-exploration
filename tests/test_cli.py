"""Tests for the dremio-client CLI."""

from click.testing import CliRunner

from dremio_client.cli import main


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
