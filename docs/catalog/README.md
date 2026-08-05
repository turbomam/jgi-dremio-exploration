# JGI lakehouse catalog dumps

Generated 2026-08-05 against `https://lakehouse.jgi.lbl.gov` with the commands in this repo. Regenerate with `just refresh-gold-catalog`.

These are metadata only: table names, column names, types, nullability. No row data.

## Files

| File | Contents |
|---|---|
| `gold-tables.tsv` | The 387 tables in `"gold-db-2 postgresql".gold` |
| `gold-columns.tsv` | 4,712 columns across all 387 of those tables |
| `img_gold-columns.tsv` | Columns for `"img-db-2 postgresql".img_gold`, which holds `gold_sequencing_project` |

## Reading `*-columns.tsv`

Columns are `TABLE_SCHEMA`, `TABLE_NAME`, `ORDINAL_POSITION`, `COLUMN_NAME`, `DATA_TYPE`, `IS_NULLABLE`, `CHARACTER_MAXIMUM_LENGTH`, `NUMERIC_PRECISION`, `NUMERIC_SCALE`, `SOURCE`.

`SOURCE` records where each row came from and is the reason to trust the dump:

- `information_schema` (2,253 rows): straight from `INFORMATION_SCHEMA.COLUMNS`.
- `describe` (2,459 rows): from `DESCRIBE TABLE`, one query per table.

The split exists because **`INFORMATION_SCHEMA.COLUMNS` covers only 245 of the 387 tables** in the GOLD schema. It omits every `dw_*` and `backup_20260731_*` table. Those tables are real and queryable: `dw_sequencing_product` returns 13 columns and 117 rows. A dump built on `INFORMATION_SCHEMA.COLUMNS` alone would be missing more than half the columns while looking complete, so `dremio columns --deep` fills the gap and labels what it did.

`CHARACTER_MAXIMUM_LENGTH` is blank for every `describe` row: `DESCRIBE TABLE` does not report it.

## Size of the whole lakehouse

6,096 schemas, of which GOLD and IMG are a small part:

| Source | Schemas |
|---|---|
| `myco-db-2 mysql` | 2,990 |
| `myco-db-1 mysql` | 2,787 |
| `myco-db-3 mysql` | 232 |
| `img-db-2 postgresql` | 16 |
| `img-db-1 mysql` | 15 |
| `portal-db-1` | 15 |
| `plant-db-7 postgresql` | 11 |
| `gold-db-2 postgresql` | 3 |

## Core GOLD tables

Column counts for the tables most likely to matter for a knowledge-graph ingest:

| Table | Columns |
|---|---|
| `organism_v2` | 207 |
| `biosample` | 162 |
| `analysis_project` | 113 |
| `project` | 99 |
| `study` | 36 |

### Names that do not exist, despite appearing in other references

Checked against this dump on 2026-08-05:

| Name | Reality |
|---|---|
| `"gold-db-2 postgresql".gold.organism` | Not present. Use `organism_v2` (207 columns). |
| `"gold-db-2 postgresql".gold.sequencing_project` | Not present. Sequencing projects are in the IMG namespace: `"img-db-2 postgresql".img_gold.gold_sequencing_project` (114 columns). |

Both names are listed as key GOLD tables in the `jgi-lakehouse` skill's `references/databases.md` ([cmungall/lakehouse-skills](https://github.com/cmungall/lakehouse-skills)), which also gives the GOLD schema as 42 tables where this dump finds 387. Confirm a path from the catalog before relying on it.

## Caveats

- A dump is a snapshot. The `backup_20260731_*` tables show the source schema does change.
- Table presence is not table access, and neither is evidence about row counts or content.
- Nothing here has been checked against GOLD's own documentation at https://gold.jgi.doe.gov/.
