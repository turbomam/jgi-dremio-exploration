# JGI Dremio Lakehouse CLI - justfile
# Run `just` to see available commands

set dotenv-load := true

# List all commands
_default:
    @just --list

# =============================================================================
# PROJECT MANAGEMENT
# =============================================================================

# Install project dependencies
[group('project')]
install:
    uv sync --group dev --group qa

# Install only runtime dependencies (no dev/qa tools)
[group('project')]
install-minimal:
    uv sync

# =============================================================================
# QA TARGETS
# =============================================================================

# Run all QA checks (linting, type checking, tests)
[group('qa')]
qa: lint typecheck test
    @echo "✓ Full QA complete"

# Alias for CI
[group('qa')]
ci: qa

# Run linting with ruff (check + format check)
[group('qa')]
lint:
    uv run ruff check src/
    uv run ruff format --check src/
    @echo "✓ Lint complete"

# Run linting and auto-fix issues
[group('qa')]
lint-fix:
    uv run ruff check --fix src/
    uv run ruff format src/
    @echo "✓ Lint fixes applied"

# Run type checking with mypy
[group('qa')]
typecheck:
    uv run mypy src/
    @echo "✓ Type check complete"

# Run tests with pytest
[group('qa')]
test:
    uv run pytest
    @echo "✓ Tests complete"

# Run tests with coverage report
[group('qa')]
test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=html
    @echo "✓ Tests with coverage complete - see htmlcov/index.html"

# Run tests with verbose output
[group('qa')]
test-verbose:
    uv run pytest -v --durations=10

# =============================================================================
# CLI COMMANDS
# =============================================================================

# Test authentication with Dremio
[group('cli')]
login:
    uv run dremio login

# Run a SQL query (limited output)
[group('cli')]
query sql:
    uv run dremio query "{{sql}}"

# Export a table to file (handles pagination)
[group('cli')]
export sql output format='json':
    uv run dremio export --sql "{{sql}}" --format {{format}} -o {{output}}

# =============================================================================
# EXAMPLES
# =============================================================================

# Export GOLD study table to JSONL
# The source is the single identifier "gold-db-2 postgresql", space included;
# "gold-db-2".postgresql.gold.study does not resolve.
[group('examples')]
example-study:
    uv run dremio export \
        --sql 'SELECT * FROM "gold-db-2 postgresql".gold.study' \
        --format json \
        -o study.jsonl
    @echo "✓ Exported to study.jsonl"

# Export GOLD biosample table to JSONL
[group('examples')]
example-biosample:
    uv run dremio export \
        --sql 'SELECT * FROM "gold-db-2 postgresql".gold.biosample LIMIT 1000' \
        --format json \
        -o biosample.jsonl
    @echo "✓ Exported to biosample.jsonl"

# =============================================================================
# CATALOG
# =============================================================================

# Every schema in the lakehouse, with table counts
[group('catalog')]
schemas out='schemas.tsv':
    uv run dremio schemas -o {{out}}
    @echo "✓ Wrote {{out}}"

# Tables in one schema, e.g. just tables 'gold-db-2 postgresql.gold'
[group('catalog')]
tables schema out='tables.tsv':
    uv run dremio tables "{{schema}}" -o {{out}}
    @echo "✓ Wrote {{out}}"

# Regenerate the committed GOLD catalog dump
[group('catalog')]
refresh-gold-catalog:
    uv run dremio tables 'gold-db-2 postgresql.gold' -o docs/catalog/gold-tables.tsv
    uv run dremio columns 'gold-db-2 postgresql.gold' --deep -o docs/catalog/gold-columns.tsv
    @echo "✓ Refreshed docs/catalog/"
