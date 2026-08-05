# The GOLD KGX extraction

Generated 2026-08-05 by `uv run dremio kgx -d kgx`. Took 18 minutes 55 seconds over the REST transport, fetching 1,399,777 source rows.

## Where the files are

```
/Users/mam/gitrepos/jgi-dremio-exploration/kgx/
    nodes.tsv        122 MB    1,086,930 rows
    edges.tsv        184 MB    1,314,901 rows
    nodes.parquet     18 MB    same content
    edges.parquet     16 MB    same content
```

The directory is excluded by `.gitignore` line 52 (`*.tsv`), and the Parquet files are excluded too. Nothing here is committed. Only the code that produces it and the catalog metadata under `docs/catalog/` are in git.

## One nodes file and one edges file, for everything

This follows the KGX convention and matches how `kg-microbe` structures a source: `bacdive` is one pair, `madin_etal` is one pair. A consumer adds a single block:

```yaml
merged_graph:
  source:
    gold:
      name: "gold"
      input:
        format: tsv
        filename:
          - kgx/nodes.tsv
          - kgx/edges.tsv
```

The `category` and `predicate` columns separate the entity types inside the single file, so a consumer who wants only part of it filters rather than asking for different files.

Node columns are `id`, `category`, `name`, `provided_by`, `xref`. Edge columns are `id`, `subject`, `predicate`, `object`, `relation`, `primary_knowledge_source`.

The `relation` column records the GOLD column each edge came from, for example `gold:organism_v2.ecosystem_path_id` or `gold:ecosystem_classification_2.parent`. Every edge is traceable to the schema, so a consumer who disagrees with a predicate choice can remap on that column without re-querying.

## What is in it

| Category | Nodes | Source |
|---|---|---|
| `biolink:IndividualOrganism` | 605,885 | `organism_v2` |
| `biolink:MaterialSample` | 279,671 | `biosample` |
| `biolink:OrganismTaxon` | 125,354 | distinct `ncbi_taxonomy_id` values |
| `biolink:Study` | 71,794 | `study` |
| `biolink:EnvironmentalFeature` | 4,226 | `ecosystem_classification_2` |

| Predicate | Edges | From |
|---|---|---|
| `biolink:in_taxon` | 605,885 | `organism_v2.ncbi_taxonomy_id` |
| `biolink:related_to` | 361,673 | `project.organism_id` joined to `master_study_id` |
| `biolink:occurs_in` | 342,880 | `organism_v2.ecosystem_path_id` |
| `biolink:subclass_of` | 4,220 | `ecosystem_classification_2.parent` |
| `biolink:derives_from` | 243 | `organism_v2.biosample_id` |

Verified: 1,086,930 unique node ids with zero duplicates, and zero edges with an endpoint missing from `nodes.tsv`. Every count matches an independent measurement taken against the lakehouse before generation.

## The ecosystem signal is thinner than the edge count suggests

342,880 `occurs_in` edges look like broad environmental coverage. They are not:

| | Edges |
|---|---|
| Total `occurs_in` | 342,880 |
| Pointing at `Unclassified` or `root` | **295,897 (86%)** |
| Pointing at a real ecosystem term | **46,983 (14%)** |

Those 46,983 spread over only **304 distinct terms**, out of 4,226 in the hierarchy. The largest real ones are Fecal (19,956), Rumen (5,439), Nasopharynx (5,274), Sediment (3,034) and Soil (2,590).

So the usable taxa-to-environment signal is about 47,000 organisms, not 343,000. Anyone planning around the raw edge count will be disappointed. Filter out `Unclassified` and `root` before assessing coverage.

## Browsing it

Both tools needed are already installed: `duckdb` 1.5.5 and `visidata` 3.4.

Query the Parquet directly. There is no import step and no database file to maintain:

```bash
cd kgx
duckdb -c "SELECT x.name AS ecosystem, count(*) AS organisms
           FROM 'edges.parquet' e JOIN 'nodes.parquet' x ON x.id = e.object
           WHERE e.predicate = 'biolink:occurs_in'
             AND x.name NOT IN ('Unclassified','root')
           GROUP BY 1 ORDER BY organisms DESC LIMIT 20;"
```

Discover the schema without opening the data, since Parquet carries it in the footer:

```bash
duckdb -c "DESCRIBE SELECT * FROM 'nodes.parquet';"
duckdb -c "SELECT * FROM parquet_schema('edges.parquet');"
duckdb -c "SELECT * FROM parquet_metadata('nodes.parquet');"   # row groups and min/max stats
```

For interactive poking, `vd nodes.parquet` opens VisiData, which handles a million rows and has frequency tables (`Shift-F` on a column) that answer most "what is in here" questions faster than SQL.

### Measured, so you do not have to re-derive it

| Form | Size | Same 2-hop aggregate |
|---|---|---|
| TSV, both files | 305 MB | 0.09 s |
| DuckDB database | 244 MB | 0.03 s |
| Parquet, both files | **34 MB** | **0.04 s** |

Parquet is nine times smaller than TSV and as fast as a native DuckDB table, which is why no `.duckdb` file is kept.

**Indices are not worth building at this size.** An indexed database and an index-free one were compared on a point lookup by `id`, a low-selectivity category filter, and a 2-hop join with aggregation. The index-free copy was faster on all three (0.02 vs 0.03, 0.01 vs 0.03, 0.03 vs 0.04 seconds). DuckDB scans columnar data with per-row-group min/max zone maps, so at a million rows the index is cost without benefit. Revisit only if the data grows by orders of magnitude.

## Known limitation: it holds everything in memory

The command accumulates all nodes and edges in Python lists and writes at the end. Peak resident memory reached 610 MB for these 1.4 million source rows. GOLD fits comfortably. IMG would not: `img_core_v400` is 244 tables including gene-level data.

Streaming is the fix, but not naively, because edges need the internal-id-to-accession maps to write `gold:Gs...` CURIEs. Those maps are 957,350 entries and roughly 96 MB. The workable shape is to build the maps while streaming nodes in one pass, then re-query and stream edges in a second, with edge deduplication moving from an in-memory set to `sort -u` over the file.
