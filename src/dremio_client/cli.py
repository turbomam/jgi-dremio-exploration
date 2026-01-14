"""JGI Dremio Lakehouse CLI."""

import json
import time
from typing import IO

import click
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()


@click.group()
@click.option("--base-url", default="https://lakehouse.jgi.lbl.gov", envvar="DREMIO_URL")
@click.pass_context
def main(ctx: click.Context, base_url: str) -> None:
    """JGI Dremio lakehouse CLI"""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url


@main.command()
@click.option("--user", prompt=True, envvar="DREMIO_USER")
@click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD")
@click.pass_context
def login(ctx: click.Context, user: str, password: str) -> None:
    """Test authentication and print token"""
    r = requests.post(f"{ctx.obj['base_url']}/apiv2/login", json={"userName": user, "password": password}, verify=False)
    r.raise_for_status()
    click.echo(f"Token: _dremio{r.json()['token']}")


@main.command()
@click.argument("sql")
@click.option("--user", prompt=True, envvar="DREMIO_USER")
@click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD")
@click.option("--cf-token", envvar="CF_AUTHORIZATION")
@click.pass_context
def query(ctx: click.Context, sql: str, user: str, password: str, cf_token: str | None) -> None:
    """Run a SQL query"""
    base = ctx.obj["base_url"]
    cookies = {"CF_Authorization": cf_token} if cf_token else {}

    # Auth
    r = requests.post(
        f"{base}/apiv2/login", json={"userName": user, "password": password}, cookies=cookies, verify=False
    )
    r.raise_for_status()
    headers = {"Authorization": f"_dremio{r.json()['token']}"}

    # Submit
    job_id = requests.post(
        f"{base}/api/v3/sql", headers=headers, json={"sql": sql}, cookies=cookies, verify=False
    ).json()["id"]
    click.echo(f"Job: {job_id}")

    # Wait
    while True:
        status = requests.get(f"{base}/api/v3/job/{job_id}", headers=headers, cookies=cookies, verify=False).json()
        if status["jobState"] in ("COMPLETED", "FAILED", "CANCELED"):
            break
        time.sleep(1)

    if status["jobState"] != "COMPLETED":
        raise click.ClickException(f"Job {status['jobState']}: {status.get('errorMessage', '')}")

    # Results
    results = requests.get(f"{base}/api/v3/job/{job_id}/results", headers=headers, cookies=cookies, verify=False).json()
    click.echo(json.dumps(results["rows"], indent=2))


@main.command()
@click.option("--sql", required=True, help="SQL query to run")
@click.option("--output", "-o", type=click.File("w"), default="-", help="Output file (default: stdout)")
@click.option("--user", prompt=True, envvar="DREMIO_USER")
@click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD")
@click.option("--cf-token", envvar="CF_AUTHORIZATION")
@click.option("--format", "fmt", type=click.Choice(["tsv", "json"]), default="tsv")
@click.pass_context
def export(
    ctx: click.Context, sql: str, output: IO[str], user: str, password: str, cf_token: str | None, fmt: str
) -> None:
    """Run SQL and export all results (handles pagination)"""
    base = ctx.obj["base_url"]
    cookies = {"CF_Authorization": cf_token} if cf_token else {}

    # Auth
    r = requests.post(
        f"{base}/apiv2/login", json={"userName": user, "password": password}, cookies=cookies, verify=False
    )
    r.raise_for_status()
    headers = {"Authorization": f"_dremio{r.json()['token']}"}

    # Submit
    job_id = requests.post(
        f"{base}/api/v3/sql", headers=headers, json={"sql": sql}, cookies=cookies, verify=False
    ).json()["id"]
    click.echo(f"Job: {job_id}", err=True)

    # Wait
    while True:
        status = requests.get(f"{base}/api/v3/job/{job_id}", headers=headers, cookies=cookies, verify=False).json()
        if status["jobState"] in ("COMPLETED", "FAILED", "CANCELED"):
            break
        time.sleep(1)

    if status["jobState"] != "COMPLETED":
        raise click.ClickException(f"Job {status['jobState']}: {status.get('errorMessage', '')}")

    row_count = status.get("rowCount", 0)
    click.echo(f"Fetching {row_count} rows...", err=True)

    # Paginate through results
    offset = 0
    limit = 500
    header_written = False

    while offset < row_count:
        results = requests.get(
            f"{base}/api/v3/job/{job_id}/results",
            headers=headers,
            cookies=cookies,
            verify=False,
            params={"offset": offset, "limit": limit},
        ).json()
        rows = results.get("rows", [])

        if not rows:
            break

        if fmt == "tsv":
            if not header_written:
                output.write("\t".join(rows[0].keys()) + "\n")
                header_written = True
            for row in rows:
                output.write("\t".join(str(v) if v is not None else "" for v in row.values()) + "\n")
        else:
            for row in rows:
                output.write(json.dumps(row) + "\n")

        offset += limit
        click.echo(f"  {min(offset, row_count)}/{row_count}", err=True)

    click.echo("Done!", err=True)


if __name__ == "__main__":
    main()
