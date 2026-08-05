"""JGI Dremio Lakehouse CLI."""

import base64
import datetime as dt
import json
import time
from collections.abc import Iterator
from typing import IO, Any

import click
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# Cloudflare Access sits in front of the lakehouse. A request without a valid
# CF_Authorization cookie is answered with HTTP 200 and an HTML sign-in page, not a
# 401, so any client that trusts the status code alone reads a block as success and
# then dies parsing JSON. Every request therefore has to carry the cookie, and every
# response has to be checked for HTML before it is parsed.
COOKIE_NAME = "CF_Authorization"


def _cookies(cf_token: str | None) -> dict[str, str]:
    return {COOKIE_NAME: cf_token} if cf_token else {}


def _json_or_die(r: requests.Response, what: str) -> Any:
    """Parse a lakehouse response, turning each failure mode into a plain message."""
    ctype = r.headers.get("content-type", "").split(";")[0].strip()
    if ctype == "text/html":
        raise click.ClickException(
            f"{what}: Cloudflare returned an HTML sign-in page (HTTP {r.status_code}), so the "
            f"request never reached Dremio.\n"
            f"{COOKIE_NAME} is missing, expired or revoked. Refresh it from the browser: open "
            f"https://lakehouse.jgi.lbl.gov/ then DevTools > Application > Cookies."
        )
    try:
        return r.json()
    except ValueError:
        raise click.ClickException(
            f"{what}: expected JSON, got HTTP {r.status_code} {ctype or 'with no content-type'}"
        ) from None


def cookie_expiry(cf_token: str) -> dt.datetime | None:
    """When the Cloudflare cookie expires, per its own claim, or None if unreadable.

    The cookie is a JWT. Only the exp claim is read; the signature is not checked and
    identity claims are ignored. An unexpired cookie can still be revoked, so this is
    a hint that saves a round trip, not proof.
    """
    parts = cf_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return dt.datetime.fromtimestamp(payload["exp"], dt.UTC)
    except Exception:
        return None


def authenticate(base: str, user: str, password: str, cf_token: str | None, verify: bool) -> str:
    """Exchange the Dremio username and password for a session token."""
    r = requests.post(
        f"{base}/apiv2/login",
        json={"userName": user, "password": password},
        cookies=_cookies(cf_token),
        verify=verify,
        timeout=60,
    )
    body = _json_or_die(r, "Dremio login")
    if "token" not in body:
        detail = body.get("errorMessage") or body.get("moreInfo") or json.dumps(body)[:200]
        raise click.ClickException(f"Cloudflare passed but Dremio rejected the login (HTTP {r.status_code}): {detail}")
    return str(body["token"])


def run_sql(
    base: str, sql: str, headers: dict[str, str], cookies: dict[str, str], verify: bool, quiet: bool = False
) -> dict[str, Any]:
    """Submit SQL, wait for the job to settle, and return its final status."""
    submitted = _json_or_die(
        requests.post(
            f"{base}/api/v3/sql", headers=headers, json={"sql": sql}, cookies=cookies, verify=verify, timeout=60
        ),
        "SQL submit",
    )
    if "id" not in submitted:
        raise click.ClickException(f"SQL submit returned no job id: {json.dumps(submitted)[:200]}")
    job_id = str(submitted["id"])
    if not quiet:
        click.echo(f"Job: {job_id}", err=True)

    while True:
        status = _json_or_die(
            requests.get(f"{base}/api/v3/job/{job_id}", headers=headers, cookies=cookies, verify=verify, timeout=60),
            "job status",
        )
        if status["jobState"] in ("COMPLETED", "FAILED", "CANCELED"):
            break
        time.sleep(1)

    if status["jobState"] != "COMPLETED":
        raise click.ClickException(f"Job {status['jobState']}: {status.get('errorMessage', '')}")
    status["id"] = job_id
    return dict(status)


@click.group()
@click.option("--base-url", default="https://lakehouse.jgi.lbl.gov", envvar="DREMIO_URL")
@click.option(
    "--insecure",
    is_flag=True,
    help="Skip TLS certificate verification (not needed; the lakehouse presents a valid public certificate)",
)
@click.pass_context
def main(ctx: click.Context, base_url: str, insecure: bool) -> None:
    """JGI Dremio lakehouse CLI"""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["verify"] = not insecure


@main.command()
@click.option("--user", prompt=True, envvar="DREMIO_USER")
@click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD")
@click.option("--cf-token", envvar="CF_AUTHORIZATION")
@click.option("--show-token", is_flag=True, help="Print the Dremio session token (a credential)")
@click.pass_context
def login(ctx: click.Context, user: str, password: str, cf_token: str | None, show_token: bool) -> None:
    """Test authentication, one layer at a time

    Reports the Cloudflare cookie's expiry, then whether Cloudflare passes the
    request, then whether Dremio accepts the username and password. A failure names
    the layer that failed, because the fixes are different: the cookie can only be
    recopied from a browser, the password cannot.
    """
    base, verify = ctx.obj["base_url"], ctx.obj["verify"]

    if not cf_token:
        click.echo(f"{COOKIE_NAME:<18} not set. Requests will be stopped at the Cloudflare edge.")
    else:
        exp = cookie_expiry(cf_token)
        if exp is None:
            click.echo(f"{COOKIE_NAME:<18} present, expiry unreadable (not a JWT)")
        else:
            days = (exp - dt.datetime.now(dt.UTC)).total_seconds() / 86400
            local = exp.astimezone().strftime("%Y-%m-%d %H:%M %Z")
            if days <= 0:
                click.echo(f"{COOKIE_NAME:<18} EXPIRED {abs(days):.1f} days ago ({local})")
            else:
                click.echo(f"{COOKIE_NAME:<18} valid for {days:.1f} more days ({local})")

    token = authenticate(base, user, password, cf_token, verify)
    click.echo(f"{'Cloudflare':<18} passed")
    click.echo(f"{'Dremio login':<18} ok, session token received ({len(token)} chars)")
    if show_token:
        click.echo(f"Token: _dremio{token}")


def fetch_rows(
    base: str,
    job_id: str,
    row_count: int,
    headers: dict[str, str],
    cookies: dict[str, str],
    verify: bool,
    page: int = 500,
    stop_after: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield a completed job's rows, following pagination to the end.

    Two measured behaviours shape this loop, both of which lose data if ignored:

    1. An unpaginated GET of /results returns only Dremio's first page. On
       2026-08-05 that was 100 of 6,096 schemas, with nothing to say the other
       5,996 existed. So always page.
    2. rowCount is not trustworthy as a stopping condition. DESCRIBE TABLE reports
       rowCount 0 in both the job status and the results payload while returning 13
       rows, so a `while offset < row_count` loop yields nothing at all. rowCount is
       therefore a hint; an empty page is the authority on where the data ends.
    """
    target = row_count if row_count > 0 else None
    if stop_after is not None:
        target = stop_after if target is None else min(target, stop_after)

    offset = 0
    while target is None or offset < target:
        ask = page if target is None else min(page, target - offset)
        results = _json_or_die(
            requests.get(
                f"{base}/api/v3/job/{job_id}/results",
                headers=headers,
                cookies=cookies,
                verify=verify,
                params={"offset": offset, "limit": ask},
                timeout=120,
            ),
            "results page",
        )
        rows = results.get("rows", [])
        if not rows:
            return
        yield from rows
        offset += len(rows)


@main.command()
@click.argument("sql")
@click.option("--user", prompt=True, envvar="DREMIO_USER")
@click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD")
@click.option("--cf-token", envvar="CF_AUTHORIZATION")
@click.option(
    "--limit", "-n", default=100, show_default=True, help="Maximum rows to print. Use `export` for a whole table."
)
@click.pass_context
def query(ctx: click.Context, sql: str, user: str, password: str, cf_token: str | None, limit: int) -> None:
    """Run a SQL query

    Prints at most --limit rows and says on stderr how many the job actually
    produced, so a partial answer never looks like a complete one.
    """
    base, verify = ctx.obj["base_url"], ctx.obj["verify"]
    cookies = _cookies(cf_token)
    headers = {"Authorization": f"_dremio{authenticate(base, user, password, cf_token, verify)}"}

    status = run_sql(base, sql, headers, cookies, verify)
    row_count = int(status.get("rowCount", 0))
    rows = list(fetch_rows(base, status["id"], row_count, headers, cookies, verify, stop_after=limit))
    click.echo(json.dumps(rows, indent=2))
    if row_count > len(rows):
        click.echo(
            f"Showing {len(rows)} of {row_count} rows. Raise --limit, or use "
            f"`dremio export` to write them all to a file.",
            err=True,
        )
    elif len(rows) == limit:
        # rowCount is unreliable (DESCRIBE reports 0), so a full page might not be
        # the whole answer. Say so rather than implying it is.
        click.echo(f"{len(rows)} rows, which is the whole --limit. There may be more.", err=True)
    else:
        click.echo(f"{len(rows)} rows.", err=True)


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
    base, verify = ctx.obj["base_url"], ctx.obj["verify"]
    cookies = _cookies(cf_token)
    headers = {"Authorization": f"_dremio{authenticate(base, user, password, cf_token, verify)}"}

    status = run_sql(base, sql, headers, cookies, verify)
    row_count = int(status.get("rowCount", 0))
    click.echo(f"Fetching {row_count} rows...", err=True)

    written = write_rows(fetch_rows(base, status["id"], row_count, headers, cookies, verify), output, fmt, row_count)
    click.echo(f"Done, wrote {written} rows.", err=True)
    check_written(written, row_count)


def check_written(written: int, row_count: int) -> None:
    """Fail loudly if fewer rows landed than the job promised.

    Only checks when rowCount is positive: DESCRIBE and friends report 0 while
    returning rows, so a strict equality check would reject a correct result.
    """
    if row_count > 0 and written != row_count:
        raise click.ClickException(f"expected {row_count} rows, wrote {written}")


def write_rows(rows: Iterator[dict[str, Any]], output: IO[str], fmt: str, row_count: int = 0) -> int:
    """Write rows as TSV or JSONL and return how many were written.

    TSV breaks on values containing tabs or newlines, which GOLD free-text columns do
    contain, so JSONL is the safe default for anything being kept.
    """
    written = 0
    header_written = False
    for row in rows:
        if fmt == "tsv":
            if not header_written:
                output.write("\t".join(row.keys()) + "\n")
                header_written = True
            output.write("\t".join(str(v) if v is not None else "" for v in row.values()) + "\n")
        else:
            output.write(json.dumps(row) + "\n")
        written += 1
        if row_count and written % 500 == 0:
            click.echo(f"  {written}/{row_count}", err=True)
    return written


# ------------------------------------------------------------------- catalog dumps
# INFORMATION_SCHEMA is the only reliable way to see what is in this Dremio. The web
# UI tree is browsable but caps at 2,000 rows per query, and the source names are not
# guessable: the GOLD source is the single identifier `gold-db-2 postgresql`, with a
# space in it, so the plausible-looking "gold-db-2".postgresql.gold.study does not
# resolve. Always confirm a path from the catalog before writing a query against it.


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_path(schema: str, table: str) -> str:
    """A fully qualified, quoted table reference.

    Source names contain spaces, so each dot-separated part is quoted separately:
    'gold-db-2 postgresql.gold' + 'study' becomes "gold-db-2 postgresql"."gold"."study".
    """
    parts = [*schema.split("."), table]
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


class Conn:
    """One authenticated session, reused across many queries.

    The catalog commands can issue hundreds of queries; re-authenticating for each
    one would be slow and would hammer the login endpoint for no reason.
    """

    def __init__(self, ctx: click.Context, user: str, password: str, cf_token: str | None) -> None:
        self.base = ctx.obj["base_url"]
        self.verify = ctx.obj["verify"]
        self.cookies = _cookies(cf_token)
        token = authenticate(self.base, user, password, cf_token, self.verify)
        self.headers = {"Authorization": f"_dremio{token}"}

    def rows(self, sql: str, quiet: bool = False) -> list[dict[str, Any]]:
        status = run_sql(self.base, sql, self.headers, self.cookies, self.verify, quiet=quiet)
        row_count = int(status.get("rowCount", 0))
        out = list(fetch_rows(self.base, status["id"], row_count, self.headers, self.cookies, self.verify))
        check_written(len(out), row_count)
        return out


def dump(
    ctx: click.Context, sql: str, user: str, password: str, cf_token: str | None, output: IO[str], fmt: str
) -> None:
    """Run a catalog query and write every row, with no silent cap."""
    rows = Conn(ctx, user, password, cf_token).rows(sql)
    written = write_rows(iter(rows), output, fmt)
    click.echo(f"{written} rows.", err=True)


catalog_options = [
    click.option("--output", "-o", type=click.File("w"), default="-", help="Output file (default: stdout)"),
    click.option("--user", prompt=True, envvar="DREMIO_USER"),
    click.option("--password", prompt=True, hide_input=True, envvar="DREMIO_PASSWORD"),
    click.option("--cf-token", envvar="CF_AUTHORIZATION"),
    click.option("--format", "fmt", type=click.Choice(["tsv", "json"]), default="tsv"),
]


def with_catalog_options(f: Any) -> Any:
    for opt in reversed(catalog_options):
        f = opt(f)
    return f


@main.command()
@click.option("--like", help="Only schemas matching this SQL LIKE pattern, e.g. 'gold-db-2%'")
@with_catalog_options
@click.pass_context
def schemas(
    ctx: click.Context, like: str | None, output: IO[str], user: str, password: str, cf_token: str | None, fmt: str
) -> None:
    """List every schema with its table count"""
    where = f"WHERE TABLE_SCHEMA LIKE {sql_literal(like)}" if like else ""
    dump(
        ctx,
        f'SELECT TABLE_SCHEMA, COUNT(*) AS n_tables FROM INFORMATION_SCHEMA."TABLES" '
        f"{where} GROUP BY TABLE_SCHEMA ORDER BY TABLE_SCHEMA",
        user,
        password,
        cf_token,
        output,
        fmt,
    )


# Foreign keys are not in Dremio's INFORMATION_SCHEMA at all: it exposes only
# CATALOGS, COLUMNS, SCHEMATA, TABLES and VIEWS, with no constraint views. The
# relationships still exist in the underlying database, and Dremio can pass a query
# straight through to a relational source, so this reads Postgres's own catalog.
FK_SQL = """
SELECT con.conname AS constraint_name,
       src.relname AS src_table, src_col.attname AS src_column,
       tgt_ns.nspname AS tgt_schema, tgt.relname AS tgt_table, tgt_col.attname AS tgt_column,
       sk.ord AS column_position
FROM pg_constraint con
JOIN pg_class src ON src.oid = con.conrelid
JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
JOIN pg_class tgt ON tgt.oid = con.confrelid
JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
JOIN unnest(con.conkey) WITH ORDINALITY AS sk(attnum, ord) ON true
JOIN unnest(con.confkey) WITH ORDINALITY AS tk(attnum, ord) ON tk.ord = sk.ord
JOIN pg_attribute src_col ON src_col.attrelid = con.conrelid AND src_col.attnum = sk.attnum
JOIN pg_attribute tgt_col ON tgt_col.attrelid = con.confrelid AND tgt_col.attnum = tk.attnum
WHERE con.contype = 'f' AND src_ns.nspname = {schema}
ORDER BY src.relname, con.conname, sk.ord
"""


def external_query(source: str, inner_sql: str) -> str:
    """Wrap a source-native query for Dremio's pass-through.

    The inner SQL becomes a single-quoted Dremio string literal, so its own single
    quotes have to be doubled.
    """
    escaped = inner_sql.replace("'", "''")
    return f"SELECT * FROM TABLE(\"{source}\".external_query('{escaped}'))"


@main.command(name="foreign-keys")
@click.argument("schema")
@with_catalog_options
@click.pass_context
def foreign_keys(
    ctx: click.Context, schema: str, output: IO[str], user: str, password: str, cf_token: str | None, fmt: str
) -> None:
    """Dump foreign keys for a schema on a PostgreSQL source

    Dremio's INFORMATION_SCHEMA has no constraint views, so this passes a query
    through to the source's own pg_catalog. That means it only works for PostgreSQL
    sources: 'gold-db-2 postgresql.gold' works, a MySQL source does not.

    Composite keys produce one row per column, ordered by column_position.
    """
    source, _, pg_schema = schema.partition(".")
    if not pg_schema:
        raise click.ClickException(f"expected 'source.schema', got {schema!r}")
    inner = FK_SQL.format(schema="'" + pg_schema.replace("'", "''") + "'")
    dump(ctx, external_query(source, inner), user, password, cf_token, output, fmt)


@main.command()
@click.argument("schema")
@with_catalog_options
@click.pass_context
def tables(
    ctx: click.Context, schema: str, output: IO[str], user: str, password: str, cf_token: str | None, fmt: str
) -> None:
    """List the tables in one schema

    SCHEMA is the exact TABLE_SCHEMA string from `dremio schemas`, spaces and all,
    for example: 'gold-db-2 postgresql.gold'
    """
    dump(
        ctx,
        f'SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA."TABLES" '
        f"WHERE TABLE_SCHEMA = {sql_literal(schema)} ORDER BY TABLE_NAME",
        user,
        password,
        cf_token,
        output,
        fmt,
    )


@main.command()
@click.argument("schema")
@click.option("--table", help="Restrict to one table (default: every table in the schema)")
@click.option(
    "--deep",
    is_flag=True,
    help="DESCRIBE tables that INFORMATION_SCHEMA.COLUMNS omits (slow: one query per missing table)",
)
@with_catalog_options
@click.pass_context
def columns(
    ctx: click.Context,
    schema: str,
    table: str | None,
    deep: bool,
    output: IO[str],
    user: str,
    password: str,
    cf_token: str | None,
    fmt: str,
) -> None:
    """Dump the column-level schema of a schema or one table

    This is the schema dump: table, column, ordinal, type, nullability.

    INFORMATION_SCHEMA.COLUMNS does not cover every table. In
    'gold-db-2 postgresql.gold' on 2026-08-05 it described 245 of the 387 tables
    that INFORMATION_SCHEMA."TABLES" lists, omitting all the dw_* and
    backup_20260731_* ones. The omitted tables are real and queryable
    (dw_sequencing_product returns 13 columns and 117 rows), so a plain dump is
    silently incomplete. --deep fills the gap with one DESCRIBE per missing table,
    which is slow but complete, and labels every row with where it came from.
    """
    conn = Conn(ctx, user, password, cf_token)
    where = f"WHERE TABLE_SCHEMA = {sql_literal(schema)}"
    if table:
        where += f" AND TABLE_NAME = {sql_literal(table)}"

    rows = conn.rows(
        "SELECT TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, DATA_TYPE, "
        "IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE "
        f"FROM INFORMATION_SCHEMA.COLUMNS {where} ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    for r in rows:
        r["SOURCE"] = "information_schema"
    described = {str(r["TABLE_NAME"]) for r in rows}

    listed = {
        str(r["TABLE_NAME"])
        for r in conn.rows(
            f'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_SCHEMA = {sql_literal(schema)}'
        )
    }
    if table:
        listed &= {table}
    missing = sorted(listed - described)

    if not missing:
        click.echo(f"{len(rows)} columns over {len(described)} tables.", err=True)
    elif not deep:
        click.echo(
            f"WARNING: {len(missing)} of {len(listed)} tables have no rows in "
            f"INFORMATION_SCHEMA.COLUMNS and are NOT in this dump "
            f"(for example: {', '.join(missing[:3])}). Re-run with --deep to include them.",
            err=True,
        )
    else:
        click.echo(f"DESCRIBEing {len(missing)} tables absent from INFORMATION_SCHEMA.COLUMNS...", err=True)
        for i, name in enumerate(missing, 1):
            try:
                described_rows = conn.rows(f"DESCRIBE TABLE {quote_path(schema, name)}", quiet=True)
            except click.ClickException as e:
                click.echo(f"  [{i}/{len(missing)}] {name}: SKIPPED ({e.message[:70]})", err=True)
                continue
            for ordinal, d in enumerate(described_rows, 1):
                rows.append(
                    {
                        "TABLE_SCHEMA": schema,
                        "TABLE_NAME": name,
                        "ORDINAL_POSITION": ordinal,
                        "COLUMN_NAME": d.get("COLUMN_NAME"),
                        "DATA_TYPE": d.get("DATA_TYPE"),
                        "IS_NULLABLE": d.get("IS_NULLABLE"),
                        "CHARACTER_MAXIMUM_LENGTH": None,
                        "NUMERIC_PRECISION": d.get("NUMERIC_PRECISION"),
                        "NUMERIC_SCALE": d.get("NUMERIC_SCALE"),
                        "SOURCE": "describe",
                    }
                )
            if i % 20 == 0:
                click.echo(f"  {i}/{len(missing)}", err=True)
        click.echo(f"{len(rows)} columns over {len({str(r['TABLE_NAME']) for r in rows})} tables.", err=True)

    rows.sort(key=lambda r: (str(r["TABLE_NAME"]), int(r["ORDINAL_POSITION"] or 0)))
    write_rows(iter(rows), output, fmt)


if __name__ == "__main__":
    main()
