# JGI Dremio Lakehouse CLI

A minimal CLI tool for querying the JGI Dremio Lakehouse via API, with support for bulk data export beyond the 2,000-row limit of the web interface.

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

### Getting the CF_AUTHORIZATION token

The `CF_AUTHORIZATION` token is a Cloudflare Access cookie required for authentication:

1. Open https://lakehouse.jgi.lbl.gov/ in your browser
2. Open Developer Tools (F12) > Application > Cookies
3. Copy the value of the `CF_Authorization` cookie

**Note:** The token lifetime is not well documented. If you get authentication errors, try refreshing the CF_AUTHORIZATION value in the `.env` file with the current token from your browser.

## Usage

### Export a table (recommended)

Export handles pagination automatically, fetching all rows:

```bash
# Export to JSONL (recommended - handles newlines in values)
uv run dremio export --sql "SELECT * FROM \"gold-db-2\".postgresql.gold.study" --format json -o study.jsonl

# Export to TSV
uv run dremio export --sql "SELECT * FROM \"gold-db-2\".postgresql.gold.biosample" --format tsv -o biosample.tsv
```

**Recommendation:** Use JSONL format (`--format json`) because TSV output can break if table values contain newline characters.

### Run a query (limited output)

For quick queries where you don't need all results:

```bash
uv run dremio query "SELECT * FROM \"gold-db-2\".postgresql.gold.study LIMIT 10"
```

### Test authentication

```bash
uv run dremio login
```

## Available Tables

Example tables in the GOLD database (`gold-db-2.postgresql.gold`):

| Table | Description |
|-------|-------------|
| `study` | GOLD studies |
| `biosample` | GOLD biosamples |
| `organism_v2` | GOLD organisms (replaces deprecated `organism` table) |
| `analysis_project` | GOLD analysis projects |

Sequencing projects can be found in a different namespace:
- `img-db-2.postgresql.img_gold.gold_sequencing_project`

For the full schema, see the [GOLD home page](https://gold.jgi.doe.gov/) or contact the GOLD team.

## API Reference

This tool uses the [Dremio REST API](https://docs.dremio.com/current/reference/api/). The current JGI instance is the open-source version; an Enterprise version with additional features is planned.

