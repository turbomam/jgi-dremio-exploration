# JGI Dremio Lakehouse CLI

A minimal CLI for querying the JGI Dremio Lakehouse over its REST API, with bulk export past the 2,000-row limit of the web interface and commands for dumping the catalog.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- JGI Dremio Lakehouse access (request via JGI)

## Setup

1. Clone this repository:
   ```bash
   git clone <repo-url>
   cd jgi-dremio-exploration
   ```

2. Create a `.env` file with your credentials:
   ```bash
   DREMIO_USER=your_username
   DREMIO_PASSWORD=your_password
   CF_AUTHORIZATION=your_cloudflare_token
   ```

3. Install dependencies:
   ```bash
   uv sync
   ```

## Authentication

Three credentials, two independent layers. Cloudflare Access sits in front of Dremio, so the cookie is a prerequisite for reaching Dremio at all, not an alternative to the username and password.

| Credential | Layer | How it is renewed |
|---|---|---|
| `CF_AUTHORIZATION` | Cloudflare Access | Copied by hand from a browser. No script can do it. |
| `DREMIO_USER` | Dremio | As issued by JGI |
| `DREMIO_PASSWORD` | Dremio | As issued by JGI |

### Getting the CF_AUTHORIZATION token

1. Open https://lakehouse.jgi.lbl.gov/ in your browser
2. Open Developer Tools (F12) > Application > Cookies
3. Copy the value of the `CF_Authorization` cookie

The cookie is a JWT and carries its own expiry. Cookies issued in mid-2026 had a **30-day** lifetime; read the real value rather than assuming, because `dremio login` prints it:

```console
$ uv run dremio login
CF_Authorization   valid for 24.5 more days (2026-08-29 21:17 EDT)
Cloudflare         passed
Dremio login       ok, session token received (26 chars)
```

An unexpired cookie can still be revoked, so `login` also exercises both layers for real.

### The failure mode worth knowing

**Cloudflare answers a request with no valid cookie using HTTP 200 and an HTML sign-in page, not a 401.** Any client that checks only the status code reads a block as success and then dies parsing JSON. This CLI checks the content type and tells you which layer refused you:

```console
$ uv run dremio login
Error: Dremio login: Cloudflare returned an HTML sign-in page (HTTP 200), so the request never reached Dremio.
CF_Authorization is missing, expired or revoked. Refresh it from the browser: open
https://lakehouse.jgi.lbl.gov/ then DevTools > Application > Cookies.
```

versus a credential that got past Cloudflare:

```console
Error: Cloudflare passed but Dremio rejected the login (HTTP 401): Login failed: Invalid username or password
```

## Finding your way around

**Table paths are not guessable, so read them out of the catalog.** Dremio source names can contain spaces, and the GOLD source is the single identifier `gold-db-2 postgresql`. The plausible-looking `"gold-db-2".postgresql.gold.study` does not resolve; the working reference is:

```sql
SELECT * FROM "gold-db-2 postgresql".gold.study
```

```bash
# Every schema and its table count (6,096 schemas as of 2026-08-05)
uv run dremio schemas -o schemas.tsv

# Just the GOLD ones
uv run dremio schemas --like 'gold-db-2%'

# Tables in one schema
uv run dremio tables 'gold-db-2 postgresql.gold' -o gold-tables.tsv

# Column-level schema dump
uv run dremio columns 'gold-db-2 postgresql.gold' --deep -o gold-columns.tsv

# Foreign keys (PostgreSQL sources only)
uv run dremio foreign-keys 'gold-db-2 postgresql.gold' -o gold-foreign-keys.tsv
```

### Where foreign keys come from

Dremio's `INFORMATION_SCHEMA` has no constraint views: `CATALOGS`, `COLUMNS`, `SCHEMATA`, `TABLES`, `VIEWS`, and nothing else. The constraints do exist in the underlying database, and Dremio can pass a query through to a relational source, so `foreign-keys` reads Postgres's `pg_catalog` directly. GOLD has 376 foreign keys across 73 tables. This works only for PostgreSQL sources; the MySQL ones (`myco-db-*`, `img-db-1`) would need a different query.

A pre-generated dump of GOLD lives in [`docs/catalog/`](docs/catalog/).

### Why `columns` has a `--deep` flag

`INFORMATION_SCHEMA.COLUMNS` does not describe every table. In `gold-db-2 postgresql.gold` on 2026-08-05 it covered 245 of the 387 tables that `INFORMATION_SCHEMA."TABLES"` lists, omitting all the `dw_*` and `backup_20260731_*` ones. The omitted tables are real and queryable: `dw_sequencing_product` returns 13 columns and 117 rows. Without `--deep` the command warns about the gap instead of quietly shipping a partial dump; with it, each missing table is filled in via `DESCRIBE TABLE` and every row is labelled in a `SOURCE` column (`information_schema` or `describe`).

## Usage

### Export a table (recommended)

Export follows pagination and writes every row:

```bash
# JSONL (recommended: safe for values containing tabs or newlines)
uv run dremio export --sql 'SELECT * FROM "gold-db-2 postgresql".gold.study' --format json -o study.jsonl

# TSV
uv run dremio export --sql 'SELECT * FROM "gold-db-2 postgresql".gold.biosample' --format tsv -o biosample.tsv
```

Use JSONL for anything you intend to keep. GOLD free-text columns do contain newlines, which break TSV.

### Run a query (capped output)

```bash
uv run dremio query 'SELECT * FROM "gold-db-2 postgresql".gold.study' -n 10
```

`query` prints at most `--limit`/`-n` rows (default 100) and always reports on stderr how many the job produced:

```
Showing 100 of 6096 rows. Raise --limit, or use `dremio export` to write them all to a file.
```

This matters because a single unpaginated fetch of Dremio's results endpoint returns only the first page and says nothing about the rest. Before this was fixed, a query over 6,096 schemas printed 100 rows and looked complete.

### Test authentication

```bash
uv run dremio login              # reports cookie expiry, Cloudflare, then Dremio
uv run dremio login --show-token # also prints the Dremio session token
```

## Available Tables

Confirmed paths (verified 2026-08-05):

| Path | Contents |
|---|---|
| `"gold-db-2 postgresql".gold` | GOLD proper: 387 tables, including `study`, `biosample`, `organism_v2`, `analysis_project`, and the `dw_*` data-warehouse views |
| `"gold-db-2 postgresql".gold_dsi` | 396 tables |
| `"img-db-2 postgresql".img_core_v400` | IMG core, 244 tables |
| `"img-db-2 postgresql".img_gold` | 120 tables, including `gold_sequencing_project` |

Notes:
- `organism_v2` replaces the deprecated `organism` table, which is not in the schema at all.
- There is no `gold.sequencing_project`. GOLD has `dw_sequencing_project`; the IMG namespace has `"img-db-2 postgresql".img_gold.gold_sequencing_project`.
- The lakehouse holds far more than GOLD and IMG: 6,096 schemas in total, dominated by `myco-db-*` (6,009 schemas across three MySQL sources).

For GOLD's own documentation see the [GOLD home page](https://gold.jgi.doe.gov/).

## Gotchas

- **A missing cookie looks like a 200.** See above.
- **`rowCount` is not a reliable stopping condition.** `DESCRIBE TABLE` reports `rowCount: 0` in both the job status and the results payload while returning rows, so a `while offset < rowCount` loop yields nothing. This CLI treats an empty page, not `rowCount`, as the end of the data.
- **TLS verification is on by default.** The lakehouse presents a valid public certificate (Google Trust Services), so the `verify=False` this client used to hard-code was unnecessary. `--insecure` still exists if a future proxy needs it.
- **Catalog queries are slow.** A schema-wide `INFORMATION_SCHEMA` query can take a minute or more; use `-o` and let it run. Arrow Flight would be faster, but see below.
- **Arrow Flight is not reachable off the LBL network.** Dremio's Flight SQL endpoint is a different host and port from the REST one: `lakehouse-1.jgi.lbl.gov:32010`, which resolves to `128.3.96.93` in LBL address space. Measured 2026-08-05 from off-VPN: port 32010 refused on that host, timed out through the Cloudflare name, and only `lakehouse.jgi.lbl.gov:443` was open. `linkml-store` ships a Flight-based `dremio` adapter (`pyarrow` + `adbc-driver-flightsql`) and it is installed here, so the client side is ready whenever the network is. Until then REST is the only off-network path.
- **Some paths need the LBL VPN.** The lakehouse itself does not, but other JGI services (the PPS/Data Warehouse gateways) do.

## API Reference

This tool uses the [Dremio REST API](https://docs.dremio.com/current/reference/api/). The JGI instance was the open-source version as of early 2026, with an Enterprise instance planned.

## Related tools

- [`cmungall/lakehouse-skills`](https://github.com/cmungall/lakehouse-skills) packages the same lakehouse as a Claude skill, querying it through `linkml-store`'s `dremio-rest://` adapter.
- [`linkml/linkml-store`](https://github.com/linkml/linkml-store) provides that adapter; see its `docs/how-to/Use-Dremio-REST.md`.

Use this CLI for bulk export, catalog dumps, and debugging an auth problem from a second code path.

## Performance, and why Arrow Flight matters

Measured 2026-08-05 against `organism_v2` (605,885 rows) over REST:

| | |
|---|---|
| Results page cap | **500 rows, hard.** `limit=1000` and `limit=5000` both return **0 rows**, not 500 and not an error |
| Throughput | 1,912 rows/sec |
| Full table | ~5.3 minutes, **1,212 sequential HTTPS round trips** through Cloudflare |

The page cap is the reason `MAX_PAGE` is a constant and not a tuning knob. Raising it to go faster returns an empty page, and since `rowCount` is also unreliable, the caller reads that as the end of the data and silently truncates.

Arrow Flight SQL is Dremio's alternative: one streamed result set of Arrow record batches over gRPC, with no per-page round trip, no 500-row ceiling, and no JSON parse. `linkml-store` ships a Flight-based `dremio` adapter and the dependencies (`pyarrow`, `adbc-driver-flightsql`) are already installed here.

It is **not reachable off the LBL network**. Flight is a different host and port from REST:

```
lakehouse-1.jgi.lbl.gov      128.3.96.93          (LBL address space)
lakehouse-1.jgi.lbl.gov:32010  refused
lakehouse.jgi.lbl.gov:32010    timed out
lakehouse.jgi.lbl.gov:443      OPEN                (the REST path, via Cloudflare)
```

To settle it from on-VPN, per `fmschulz/omics-skills` `docs/arrow-flight-python.md`:

```bash
uv run --with "dremio-flight @ https://github.com/dremio-hub/arrow-flight-client-examples/releases/download/dremio-flight-python-v1.1.0/dremio_flight-1.1.0-py3-none-any.whl" - <<'PY'
import os
from dremio.flight.connection import DremioFlightEndpointConnection
conn = DremioFlightEndpointConnection({
    "hostname": "lakehouse-1.jgi.lbl.gov",
    "username": os.environ["DREMIO_USER"],
    "password": os.environ["DREMIO_PASSWORD"],
})
print(conn.query("SELECT 1"))
PY
```

If that returns, time the same `organism_v2` query and compare against 1,912 rows/sec. Note Flight uses username and password only; no Cloudflare cookie is involved, because it does not go through Cloudflare.
