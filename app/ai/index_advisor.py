"""
Index Advisor & Optimization Engine.
Performs database-wide and table-level static analysis for duplicate,
redundant, missing foreign-key, unused, and low-cardinality indexes.
Generates safe DDL migrations and rich AI advisory prompts.
"""
from __future__ import annotations
import re
from typing import Any
from sqlalchemy import text, Engine
from app.db import schema_reader as sr


def scan_database_indexes(engine: Engine, database: str = "") -> dict[str, list[dict]]:
    """
    Performs a database-wide index health scan across all tables.
    Returns categorized findings.
    """
    dialect = engine.dialect.name
    tables = sr.list_tables(engine, database)

    findings = {
        "duplicates": [],
        "redundant": [],
        "missing_fk": [],
        "low_cardinality": [],
        "over_indexed": [],
    }

    for tbl in tables:
        indexes = sr.get_indexes(engine, database, tbl)
        fks = sr.get_foreign_keys(engine, database, tbl)
        columns = sr.get_columns(engine, database, tbl)

        # 1. Duplicates & Redundant
        red_issues = find_redundant_indexes(indexes, columns=columns, table=tbl, dialect=dialect, database=database)
        for issue in red_issues:
            if issue["type"] == "Duplicate":
                findings["duplicates"].append({**issue, "table": tbl})
            elif issue["type"] == "Redundant":
                findings["redundant"].append({**issue, "table": tbl})
            elif issue["type"] == "Over-indexed":
                findings["over_indexed"].append({**issue, "table": tbl})

        # 2. Missing FK indexes
        fk_issues = find_missing_fk_indexes(indexes, fks, tbl, dialect, database)
        for issue in fk_issues:
            findings["missing_fk"].append({**issue, "table": tbl})

        # 3. Low cardinality indexes
        low_card_issues = find_low_cardinality_indexes(indexes, columns, tbl, dialect, database)
        for issue in low_card_issues:
            findings["low_cardinality"].append({**issue, "table": tbl})

    return findings


def find_redundant_indexes(
    indexes: list[dict],
    columns: list[dict] | str | None = None,
    table: str = "",
    dialect: str = "sqlite",
    database: str = "",
) -> list[dict]:
    """
    Detect redundant and duplicate indexes with full protection for PRIMARY keys,
    UNIQUE constraints, and AUTO_INCREMENT columns.
    """
    if isinstance(columns, str):
        # Backward compatibility when columns was omitted and table was passed as 2nd arg
        database = dialect
        dialect = table or "sqlite"
        table = columns
        columns = []

    issues = []

    # Identify primary keys and auto_increment columns
    auto_inc_cols: set[str] = set()
    if columns and isinstance(columns, list):
        for c in columns:
            if "auto_increment" in c.get("extra", "").lower():
                auto_inc_cols.add(c["column"].lower())
            elif c.get("key") == "PRI" and "INT" in c.get("type", "").upper() and dialect == "sqlite":
                auto_inc_cols.add(c["column"].lower())

    # Map leading column frequencies across existing indexes
    leading_cols_count: dict[str, int] = {}
    for idx in indexes:
        cols = idx.get("columns", [])
        if cols:
            lead = cols[0].lower()
            leading_cols_count[lead] = leading_cols_count.get(lead, 0) + 1

    idx_cols = [(i, tuple(c.lower() for c in i.get("columns", []))) for i in indexes]

    for i, (idx_a, cols_a) in enumerate(idx_cols):
        if not cols_a:
            continue

        name_a = idx_a.get("name", "")

        # Guardrail 1: NEVER drop PRIMARY KEY or internal auto-indexes
        if name_a.upper() == "PRIMARY" or name_a.startswith("sqlite_autoindex_"):
            continue

        # Guardrail 2: Do NOT drop index if it is the sole key on an AUTO_INCREMENT column
        if cols_a[0] in auto_inc_cols and leading_cols_count.get(cols_a[0], 0) <= 1:
            continue

        for j, (idx_b, cols_b) in enumerate(idx_cols):
            if i == j or not cols_b:
                continue

            name_b = idx_b.get("name", "")

            # Exact duplicate
            if cols_a == cols_b and name_a != name_b:
                # If idx_a is UNIQUE and idx_b is not UNIQUE, keep idx_a and drop idx_b
                if idx_a.get("unique") and not idx_b.get("unique"):
                    continue

                issues.append({
                    "type": "Duplicate",
                    "index": name_a,
                    "columns": list(cols_a),
                    "reason": f"Exact duplicate of `{name_b}` — both index {list(cols_a)}.",
                    "action": _drop_index_sql(name_a, table, dialect, database),
                })
                break

            # A is a prefix of B (B covers all of A's queries)
            if len(cols_a) < len(cols_b) and cols_b[:len(cols_a)] == cols_a:
                # Guardrail 3: Never drop a UNIQUE constraint (it enforces business data uniqueness)
                if idx_a.get("unique"):
                    continue

                issues.append({
                    "type": "Redundant",
                    "index": name_a,
                    "columns": list(cols_a),
                    "reason": (
                        f"`{name_b}` {list(cols_b)} already covers all queries that would use "
                        f"`{name_a}` {list(cols_a)} (prefix match)."
                    ),
                    "action": _drop_index_sql(name_a, table, dialect, database),
                })
                break

    # Over-indexing warning
    non_pk = [i for i in indexes if i.get("name", "").upper() not in ("PRIMARY", "SQLITE_AUTOINDEX_")]
    if len(non_pk) > 5:
        issues.append({
            "type": "Over-indexed",
            "index": f"({len(non_pk)} indexes)",
            "columns": [],
            "reason": (
                f"Table has {len(non_pk)} non-PK indexes. "
                "Each index slows down INSERT/UPDATE/DELETE operations and inflates storage."
            ),
            "action": f"-- Review and prune unused indexes on `{table}`",
        })

    return issues


def find_missing_fk_indexes(
    indexes: list[dict], foreign_keys: list[dict], table: str = "", dialect: str = "sqlite", database: str = ""
) -> list[dict]:
    """
    Find foreign keys without a leading indexed column.
    """
    leading_cols = {
        idx["columns"][0].lower()
        for idx in indexes
        if idx.get("columns")
    }

    missing = []
    for fk in foreign_keys:
        col = fk["column"]
        if col.lower() not in leading_cols:
            idx_name = f"idx_{table}_{col}"
            missing.append({
                "type": "Missing FK index",
                "index": idx_name,
                "column": col,
                "columns": [col],
                "reason": (
                    f"Foreign key `{col}` -> `{fk['ref_table']}({fk['ref_column']})` has no leading index. "
                    "Causes full table scans during JOINs and cascade operations."
                ),
                "action": _create_index_sql(idx_name, table, [col], dialect, database),
            })
    return missing


def find_low_cardinality_indexes(
    indexes: list[dict], columns: list[dict], table: str = "", dialect: str = "sqlite", database: str = ""
) -> list[dict]:
    """
    Flags single-column indexes on boolean, tinyint, or flag status columns.
    """
    issues = []
    col_map = {c["column"].lower(): c for c in columns}

    for idx in indexes:
        name = idx.get("name", "")
        # Never flag PRIMARY or unique indexes
        if name.upper() == "PRIMARY" or name.startswith("sqlite_autoindex_") or idx.get("unique"):
            continue

        cols = idx.get("columns", [])
        if len(cols) == 1:
            col_info = col_map.get(cols[0].lower())
            if col_info:
                # Do not flag if column is auto_increment or PK
                if "auto_increment" in col_info.get("extra", "").lower() or col_info.get("key") == "PRI":
                    continue

                col_type = col_info.get("type", "").upper()
                col_name = col_info["column"].lower()
                if any(b in col_name for b in ("is_", "has_", "active", "enabled", "deleted")) or col_type in ("BOOLEAN", "TINYINT(1)", "BIT"):
                    issues.append({
                        "type": "Low Cardinality Index",
                        "index": name,
                        "columns": cols,
                        "reason": (
                            f"Index `{name}` is on single boolean/flag column `{cols[0]}`. "
                            "Low selectivity often causes query planners to ignore the index in favor of sequential scans."
                        ),
                        "action": _drop_index_sql(name, table, dialect, database),
                    })
    return issues


def _drop_index_sql(index_name: str, table: str, dialect: str, database: str = "") -> str:
    if dialect == "mysql":
        tbl = f"`{database}`.`{table}`" if database else f"`{table}`"
        return f"DROP INDEX `{index_name}` ON {tbl};"
    elif dialect == "postgresql":
        return f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}";'
    else:  # sqlite
        return f'DROP INDEX IF EXISTS "{index_name}";'


def _create_index_sql(index_name: str, table: str, columns: list[str], dialect: str, database: str = "") -> str:
    if dialect == "mysql":
        tbl = f"`{database}`.`{table}`" if database else f"`{table}`"
        cols_str = ", ".join(f"`{c}`" for c in columns)
        return f"CREATE INDEX `{index_name}` ON {tbl} ({cols_str});"
    elif dialect == "postgresql":
        tbl = f'"{table}"'
        cols_str = ", ".join(f'"{c}"' for c in columns)
        return f'CREATE INDEX CONCURRENTLY "{index_name}" ON {tbl} ({cols_str});'
    else:  # sqlite
        tbl = f'"{table}"'
        cols_str = ", ".join(f'"{c}"' for c in columns)
        return f'CREATE INDEX "{index_name}" ON {tbl} ({cols_str});'


# ---------------------------------------------------------------------------
# AI Prompt Builder — Conservative Enterprise Database Architect
# ---------------------------------------------------------------------------

def build_index_prompt(
    db_type: str,
    table: str,
    columns: list[dict],
    indexes: list[dict],
    foreign_keys: list[dict],
    stats: dict,
    static_issues: list[dict] | None = None,
) -> str:
    col_lines = "\n".join(
        f"  - {c['column']} ({c['type']})"
        f"{'  [PK]' if c['key'] == 'PRI' else ''}"
        f"{'  [AUTO_INCREMENT]' if 'auto_increment' in c.get('extra', '').lower() else ''}"
        f"{'  [FK → ' + next((fk['ref_table'] for fk in foreign_keys if fk['column'] == c['column']), '') + ']' if any(fk['column'] == c['column'] for fk in foreign_keys) else ''}"
        f"{'  NOT NULL' if not c['nullable'] else ''}"
        for c in columns
    )

    idx_lines = "\n".join(
        f"  - {i['name']} ({'UNIQUE ' if i.get('unique') else ''}{i.get('type', 'BTREE')}) "
        f"on ({', '.join(i.get('columns', []))})"
        for i in indexes
    ) or "  (none)"

    fk_lines = "\n".join(
        f"  - {fk['column']} → {fk['ref_table']}({fk['ref_column']})"
        for fk in foreign_keys
    ) or "  (none)"

    approx_rows = stats.get("approx_rows", "unknown")
    data_mb = round(stats.get("data_size_bytes", 0) / 1_048_576, 2)

    # Check unindexed foreign keys
    unindexed_fks = [
        fk["column"]
        for fk in foreign_keys
        if not any(i.get("columns", [""])[0].lower() == fk["column"].lower() for i in indexes)
    ]

    static_text = ""
    if static_issues:
        static_text = "\nDeterministic Rule Findings:\n" + "\n".join(
            f"  - [{s['type']}] {s['index']}: {s['reason']}" for s in static_issues
        ) + "\n"
    else:
        static_text = "\nDeterministic Rule Findings: 0 static issues found. All Foreign Keys are indexed and no exact duplicates exist.\n"

    return f"""You are a conservative Principal {db_type.upper()} Database Performance Architect.
Your role is to maximize query performance while strictly minimizing index overhead, write amplification, and RAM consumption.

CORE ARCHITECTURAL PHILOSOPHY:
1. INDEX AUSTERITY: Every index incurs a write penalty on INSERT, UPDATE, and DELETE operations and consumes buffer pool memory. Do NOT recommend speculative composite indexes.
2. CONVERGENCE & STABILITY: If the table already has balanced indexing (Primary Key + Foreign Keys + key lookup fields), your duty is to declare the table OPTIMAL. Do not propose changes just to appear active.
3. SPECULATIVE INDEXING FORBIDDEN: Do not create multi-column permutation indexes (e.g. combining random columns like first_name, last_name, status) without proven high-cardinality search patterns.
4. ABSOLUTE CATALOG INTEGRITY: Only recommend dropping index names that EXACTLY match one of the 'Current indexes' listed below. Never invent or hallucinate index names.
5. NEVER DROP PRIMARY KEYS, UNIQUE KEYS, OR AUTO_INCREMENT KEYS: Primary keys and Unique constraints protect data integrity and must NEVER be dropped.

TABLE PROFILE:
- Table Name: {table}
- Row Count (approx): {approx_rows}
- Data Size: {data_mb} MB
- Unindexed Foreign Keys: {', '.join(unindexed_fks) if unindexed_fks else 'None (All FKs are indexed)'}

COLUMNS:
{col_lines}

CURRENT INDEXES:
{idx_lines}

FOREIGN KEYS:
{fk_lines}
{static_text}

INSTRUCTIONS FOR OUTPUT:
Respond in plain text with the four exact sections below. If no changes are genuinely necessary, explicitly write "None identified."

VERDICT
State in 1-2 clear sentences whether this table is already optimal, over-indexed, or missing a critical index. If the table is well-indexed, explicitly state: "The current index strategy is optimal and well-balanced."

MISSING INDEXES
Only list an index if there is a severe, unambiguous structural deficiency (such as an unindexed Foreign Key or critical lookup key). Write a single optimized CREATE INDEX statement in {db_type.upper()} syntax and the concrete performance justification.
If the existing indexes are sufficient, respond ONLY with: "None identified."

INDEXES TO DROP
Only list an index if it is a 100% exact duplicate or direct left-prefix redundant index that was NOT already dropped. Write the DROP INDEX statement with justification.
If there are no redundant indexes to drop, respond ONLY with: "None identified."

QUERY FILTER & SORTING RECOMMENDATIONS
Provide 1-2 sentences of guidance on query patterns that benefit from the existing indexing structure, or when composite indexing would be justified if query volume increases.
"""
