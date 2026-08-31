"""
Multi-Stage Data Type Optimizer & Storage Reducer Engine.
Performs two-phase analysis:
  Phase 1: Fast initial sample scan to identify candidate optimization targets.
  Phase 2: Deep server-side SQL aggregate verification across 100% of table rows
           (MIN, MAX, CHAR_LENGTH, Null counts, Empty counts, Numeric regex with Empty/NULL->0 treatment, Enum cardinality).

Key Architecture:
  - Single-Pass Convergence: Computes the ultimate unified end-state (Type + Shrink + Nullability + Sanitization) in one pass so you never need to re-run multiple times.
  - Database-Wide Scanner: Evaluates all tables across the entire database at once.
  - Full Safety Guardrails: Leading zeros, Auto-increment headroom, pre-flight nullability unlocking.
"""
from __future__ import annotations
import re
from typing import Any
import pandas as pd
from sqlalchemy import text, Engine
from app.db import schema_reader as sr
from app.db.size_analyzer import format_bytes


def scan_database_column_types(
    engine: Engine,
    database: str = "",
    deep_verify: bool = True,
    progress_callback: Any = None,
) -> dict[str, list[dict]]:
    """
    Scans ALL tables in the database in a single sweep and returns all type optimizations grouped by table.
    """
    tables = sr.list_tables(engine, database)
    all_suggestions: dict[str, list[dict]] = {}
    total = len(tables)

    for i, tbl in enumerate(tables):
        if progress_callback:
            progress_callback(i + 1, total, tbl)
        suggs = profile_table_columns(engine, database, tbl, sample_limit=1000, deep_verify=deep_verify)
        if suggs:
            all_suggestions[tbl] = suggs

    return all_suggestions


def profile_table_columns(
    engine: Engine,
    database: str,
    table: str,
    sample_limit: int = 1000,
    deep_verify: bool = True,
) -> list[dict]:
    """
    Analyzes all columns in a table and produces complete, single-pass unified recommendations.
    Combines base type downcasting, string shrinking, empty-string sanitization, and nullability tightening into a single DDL statement.
    """
    dialect = engine.dialect.name
    columns = sr.get_columns(engine, database, table)
    stats = sr.get_table_stats(engine, database, table)
    total_rows = stats.get("approx_rows", 0)

    if not columns:
        return []

    tbl_quoted = f"`{database}`.`{table}`" if dialect == "mysql" and database else (f"`{table}`" if dialect == "mysql" else f'"{table}"')

    # Fetch initial sample
    sample_df = pd.DataFrame()
    with engine.connect() as conn:
        try:
            sql = f"SELECT * FROM {tbl_quoted} LIMIT {sample_limit}"
            result = conn.execute(text(sql))
            rows = result.fetchall()
            sample_df = pd.DataFrame(rows, columns=list(result.keys()))
        except Exception:
            pass

    if sample_df.empty and total_rows > 0:
        return []

    sample_size = len(sample_df)
    suggestions: list[dict] = []

    for col in columns:
        col_name = col["column"]
        col_type = col.get("type", "").upper()
        is_pk = col.get("key") == "PRI"
        is_nullable = col.get("nullable", True)
        is_auto_inc = "auto_increment" in col.get("extra", "").lower()
        current_bytes = _estimate_type_bytes(col_type)

        if col_name not in sample_df.columns:
            continue

        series = sample_df[col_name].dropna()
        col_quoted = f"`{col_name}`" if dialect == "mysql" else f'"{col_name}"'

        # Baseline metrics
        target_base_type = col_type
        target_bytes = current_bytes
        category = "Data Type Optimization"
        reason_parts = []
        pre_sql = ""
        verification_badge = "Sampled Profile"

        null_count = 0
        empty_count = 0

        # ===================================================================
        # 1. Evaluate Integer Columns
        # ===================================================================
        if any(t in col_type for t in ("INT", "BIGINT", "INTEGER", "SMALLINT", "TINYINT")):
            min_val = int(series.min()) if not series.empty and pd.api.types.is_numeric_dtype(series) else 0
            max_val = int(series.max()) if not series.empty and pd.api.types.is_numeric_dtype(series) else 0
            
            if deep_verify and total_rows > sample_limit:
                try:
                    with engine.connect() as conn:
                        agg_sql = f"SELECT MIN({col_quoted}), MAX({col_quoted}), COUNT(CASE WHEN {col_quoted} IS NULL THEN 1 END) FROM {tbl_quoted}"
                        res = conn.execute(text(agg_sql)).fetchone()
                        if res and res[0] is not None and res[1] is not None:
                            min_val = int(res[0])
                            max_val = int(res[1])
                            null_count = int(res[2] or 0)
                            verification_badge = "100% Full-Table Verified"
                except Exception:
                    pass
            else:
                null_count = sample_size - len(series)

            # Integer Downcasting Evaluation
            if is_auto_inc:
                if "BIGINT" in col_type and 0 <= min_val and max_val <= 2000000000:
                    target_base_type = "INT UNSIGNED" if dialect == "mysql" else "INTEGER"
                    target_bytes = 4
                    category = "Integer Downcast (Auto-Inc Protected)"
                    reason_parts.append(f"Range [{min_val:,} to {max_val:,}] fits safely in INT UNSIGNED with 4.29B growth capacity.")
            elif "BIGINT" in col_type or ("INT" in col_type and "TINY" not in col_type and "SMALL" not in col_type):
                if 0 <= min_val and max_val <= 255:
                    target_base_type = "TINYINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 1 if dialect == "mysql" else 2
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in TINYINT).")
                elif -128 <= min_val and max_val <= 127:
                    target_base_type = "TINYINT" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 1 if dialect == "mysql" else 2
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in TINYINT).")
                elif 0 <= min_val and max_val <= 65535:
                    target_base_type = "SMALLINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 2
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in SMALLINT).")
                elif -32768 <= min_val and max_val <= 32767:
                    target_base_type = "SMALLINT"
                    target_bytes = 2
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in SMALLINT).")
                elif dialect == "mysql" and "BIGINT" in col_type and 0 <= min_val and max_val <= 16777215:
                    target_base_type = "MEDIUMINT UNSIGNED"
                    target_bytes = 3
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in MEDIUMINT).")
                elif "BIGINT" in col_type and 0 <= min_val and max_val <= 4294967295:
                    target_base_type = "INT UNSIGNED" if dialect == "mysql" else "INTEGER"
                    target_bytes = 4
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in INT).")
                elif "BIGINT" in col_type and -2147483648 <= min_val and max_val <= 2147483647:
                    target_base_type = "INT" if dialect != "postgresql" else "INTEGER"
                    target_bytes = 4
                    category = "Integer Downcast"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in INT).")

        # ===================================================================
        # 2. Evaluate String & Text Columns
        # ===================================================================
        elif any(t in col_type for t in ("VARCHAR", "TEXT", "CHAR", "STRING")):
            str_s = series.astype(str)
            max_len = int(str_s.str.len().max()) if not str_s.empty else 0
            min_len = int(str_s.str.len().min()) if not str_s.empty else 0
            avg_len = float(str_s.str.len().mean()) if not str_s.empty else 0.0

            declared_len = 255
            m = re.search(r"\((\d+)\)", col_type)
            if m:
                declared_len = int(m.group(1))
            elif "TEXT" in col_type:
                declared_len = 65535

            is_phone_or_zip = any(k in col_name.lower() for k in ("phone", "tel", "mobile", "fax", "zip", "postal", "ssn", "pin", "barcode", "card"))

            if deep_verify and dialect == "mysql":
                try:
                    with engine.connect() as conn:
                        len_fn = "CHAR_LENGTH"
                        agg_sql = (
                            f"SELECT "
                            f"COUNT(CASE WHEN {col_quoted} IS NULL THEN 1 END) AS null_cnt, "
                            f"COUNT(CASE WHEN {col_quoted} = '' THEN 1 END) AS empty_cnt, "
                            f"MIN({len_fn}({col_quoted})), "
                            f"MAX({len_fn}({col_quoted})), "
                            f"AVG({len_fn}({col_quoted})), "
                            f"COUNT(CASE WHEN {col_quoted} IS NOT NULL AND {col_quoted} != '' AND {col_quoted} NOT REGEXP '^[0-9]+$' THEN 1 END) AS non_digit_cnt, "
                            f"MIN(CAST(NULLIF({col_quoted}, '') AS UNSIGNED)) AS min_num, "
                            f"MAX(CAST(NULLIF({col_quoted}, '') AS UNSIGNED)) AS max_num, "
                            f"COUNT(CASE WHEN {col_quoted} IS NOT NULL AND {col_quoted} != '' AND {col_quoted} NOT REGEXP '^[0-9]+(\\.[0-9]+)?$' THEN 1 END) AS non_dec_cnt, "
                            f"COUNT(CASE WHEN {col_quoted} LIKE '0%' AND CHAR_LENGTH({col_quoted}) > 1 THEN 1 END) AS leading_zero_cnt "
                            f"FROM {tbl_quoted}"
                        )
                        r = conn.execute(text(agg_sql)).fetchone()
                        if r:
                            null_count = int(r[0] or 0)
                            empty_count = int(r[1] or 0)
                            min_len = int(r[2] or 0)
                            max_len = int(r[3] or 0)
                            avg_len = float(r[4] or 0.0)
                            non_digit_cnt = int(r[5] or 0)
                            min_n = int(r[6] or 0) if r[6] is not None else 0
                            max_n = int(r[7] or 0) if r[7] is not None else 0
                            non_dec_cnt = int(r[8] or 0)
                            leading_zeros = int(r[9] or 0)
                            verification_badge = "100% Full-Table Verified"

                            # 2A: Pure numeric check (without leading zeros)
                            if not is_pk and max_len <= 18 and not is_phone_or_zip and non_digit_cnt == 0 and leading_zeros == 0 and max_len > 0:
                                min_eff = min(0, min_n) if (empty_count > 0 or null_count > 0) else min_n
                                max_eff = max(0, max_n)
                                if 0 <= min_eff and max_eff <= 255:
                                    target_base_type, target_bytes = "TINYINT UNSIGNED", 1
                                elif 0 <= min_eff and max_eff <= 65535:
                                    target_base_type, target_bytes = "SMALLINT UNSIGNED", 2
                                elif 0 <= min_eff and max_eff <= 16777215:
                                    target_base_type, target_bytes = "MEDIUMINT UNSIGNED", 3
                                elif 0 <= min_eff and max_eff <= 4294967295:
                                    target_base_type, target_bytes = "INT UNSIGNED", 4
                                else:
                                    target_base_type, target_bytes = "BIGINT UNSIGNED", 8

                                category = "String to Integer Conversion"
                                reason_parts.append(f"Contains 100% numeric digits (Min: {min_n:,}, Max: {max_n:,}). Converting to native {target_base_type}.")

                            # 2B: Pure decimal check
                            elif not is_pk and max_len <= 18 and not is_phone_or_zip and non_dec_cnt == 0 and non_digit_cnt > 0 and leading_zeros == 0:
                                target_base_type, target_bytes = "DECIMAL(12, 2)", 6
                                category = "String to Decimal Conversion"
                                reason_parts.append("Contains 100% decimal numbers stored as string.")

                except Exception:
                    pass
            else:
                null_count = sample_size - len(series)
                empty_count = int((str_s == "").sum())

            # 2C: String-to-Enum conversion check
            if target_base_type == col_type and not is_pk and max_len <= 20 and min_len >= 1 and not is_phone_or_zip and dialect == "mysql":
                try:
                    with engine.connect() as conn:
                        d_sql = f"SELECT DISTINCT {col_quoted} FROM {tbl_quoted} WHERE {col_quoted} IS NOT NULL AND {col_quoted} != '' LIMIT 8"
                        distinct_vals = {str(r[0]) for r in conn.execute(text(d_sql)).fetchall() if r[0] is not None}
                        if 2 <= len(distinct_vals) <= 5 and all(len(v) <= 20 for v in distinct_vals):
                            enum_opts = ", ".join(f"'{v}'" for v in sorted(distinct_vals))
                            target_base_type = f"ENUM({enum_opts})"
                            target_bytes = 1
                            category = "String to ENUM Conversion"
                            reason_parts.append(f"Only {len(distinct_vals)} distinct values ({', '.join(sorted(distinct_vals))}).")
                except Exception:
                    pass

            # 2D: UUID format check
            if target_base_type == col_type and max_len == 36 and min_len == 36 and str_s.str.contains(r"^[0-9a-fA-F-]{36}$").all():
                target_base_type = "UUID" if dialect == "postgresql" else "CHAR(36)"
                target_bytes = 36
                category = "UUID Optimization"
                reason_parts.append("Matches exact 36-char UUID format.")

            # 2E: Oversized string shrink check
            if target_base_type == col_type and declared_len >= 80 and max_len <= 35:
                safe_len = max(max_len + 15, 30)
                safe_len = ((safe_len + 9) // 10) * 10  # Round up to 10s
                target_base_type = f"VARCHAR({safe_len})"
                target_bytes = max(int((declared_len - safe_len) * 0.4), 2)
                category = "Oversized String Shrink"
                reason_parts.append(f"Max observed length is only {max_len} chars (allocated as {col_type}).")

        # ===================================================================
        # 3. Currency / Float Precision Optimization
        # ===================================================================
        elif any(t in col_type for t in ("FLOAT", "DOUBLE", "REAL")):
            if any(k in col_name.lower() for k in ("price", "amount", "cost", "total", "fee", "tax", "balance", "salary", "rate")):
                target_base_type = "DECIMAL(12, 2)" if dialect != "sqlite" else "DECIMAL"
                target_bytes = 6
                category = "Financial Decimal Accuracy"
                reason_parts.append(f"Represents financial currency data (replaces imprecise {col_type}).")

        # ===================================================================
        # 4. Compute Optimal Unified Nullability & Sanitization (Single Pass!)
        # ===================================================================
        is_flag_or_counter = any(k in col_name.lower() for k in ("is_", "has_", "active", "enabled", "deleted", "flag", "count", "qty", "points", "attempts", "retry"))

        # Determine target nullability
        if null_count == 0 and empty_count == 0 and not is_pk and total_rows > 0:
            target_null_clause = "NOT NULL"
            target_default = ""
            if is_nullable:
                reason_parts.append("Contains 0 NULLs and 0 empty strings (tightened to NOT NULL).")
        elif empty_count > 0:
            if is_flag_or_counter:
                target_null_clause = "NOT NULL"
                target_default = "DEFAULT 0" if "INT" in target_base_type or "DECIMAL" in target_base_type else ""
                pre_sql = f"UPDATE {tbl_quoted} SET {col_quoted} = '0' WHERE {col_quoted} = '' OR {col_quoted} IS NULL;"
                reason_parts.append(f"Converted {empty_count:,} empty strings and {null_count:,} NULLs to 0 (status flag/counter).")
            else:
                target_null_clause = "NULL"
                target_default = "DEFAULT NULL"
                # If currently NOT NULL, must unlock first before updating '' to NULL
                if not is_nullable:
                    pre_sql = f"ALTER TABLE {tbl_quoted} MODIFY COLUMN {col_quoted} {col_type} NULL; UPDATE {tbl_quoted} SET {col_quoted} = NULL WHERE {col_quoted} = '';"
                else:
                    pre_sql = f"UPDATE {tbl_quoted} SET {col_quoted} = NULL WHERE {col_quoted} = '';"
                reason_parts.append(f"Sanitized {empty_count:,} empty strings into NULLs (preserves missing-value semantics).")
        else:
            target_null_clause = "NULL" if is_nullable else "NOT NULL"
            target_default = "DEFAULT NULL" if is_nullable else ""

        # Build full suggested type string
        auto_inc_clause = " AUTO_INCREMENT" if is_auto_inc and dialect == "mysql" else ""
        suggested_full_type = f"{target_base_type} {target_null_clause}{auto_inc_clause}".strip()
        current_full_type = f"{col_type} {'NULL' if is_nullable else 'NOT NULL'}{auto_inc_clause}".strip()

        # Check if any optimization occurred
        is_type_changed = target_base_type != col_type
        is_null_changed = (target_null_clause == "NOT NULL" and is_nullable) or (target_null_clause == "NULL" and not is_nullable)
        is_sanitized = empty_count > 0

        if is_type_changed or is_null_changed or is_sanitized:
            saved_per_row = max(current_bytes - target_bytes, 0)
            if not is_type_changed and is_null_changed and target_null_clause == "NOT NULL":
                saved_per_row = 1  # 1 byte for null bitmap
            if not is_type_changed and is_sanitized and "CHAR" in col_type:
                saved_per_row = 2

            est_total_saved = saved_per_row * total_rows

            # Build unified migration SQL
            d_clause = f" {target_default}" if target_default else ""
            if dialect == "mysql":
                alter_stmt = f"ALTER TABLE {tbl_quoted} MODIFY COLUMN {col_quoted} {target_base_type} {target_null_clause}{auto_inc_clause}{d_clause};"
            elif dialect == "postgresql":
                alter_stmt = f'ALTER TABLE {tbl_quoted} ALTER COLUMN "{col_name}" TYPE {target_base_type};'
            else:
                alter_stmt = f'-- SQLite: Rebuild required for "{col_name}" to {target_base_type}'

            full_sql = f"{pre_sql} {alter_stmt}".strip()

            suggestions.append({
                "table": table,
                "column": col_name,
                "category": category if is_type_changed else ("Sanitize Empty Strings to NULL" if is_sanitized else "Tighten Nullability"),
                "current_type": current_full_type,
                "suggested_type": suggested_full_type,
                "verification": verification_badge,
                "reason": " ".join(reason_parts),
                "saved_bytes_per_row": saved_per_row,
                "est_total_saved_bytes": est_total_saved,
                "formatted_savings": format_bytes(est_total_saved) if est_total_saved > 0 else "Integrity & Index Speedup",
                "sql": full_sql,
            })

    return suggestions


def _estimate_type_bytes(col_type: str) -> int:
    t = col_type.upper()
    if "TINYINT" in t:
        return 1
    if "SMALLINT" in t:
        return 2
    if "MEDIUMINT" in t:
        return 3
    if "BIGINT" in t:
        return 8
    if "INT" in t:
        return 4
    if "DOUBLE" in t or "FLOAT" in t or "REAL" in t:
        return 8
    if "DECIMAL" in t:
        return 6
    if "DATETIME" in t or "TIMESTAMP" in t:
        return 8
    if "DATE" in t:
        return 4
    if "VARCHAR" in t:
        m = re.search(r"\((\d+)\)", t)
        return int(m.group(1)) if m else 50
    if "TEXT" in t:
        return 64
    return 16


def generate_type_migration_script(engine: Engine, table: str, suggestions: list[dict], database: str = "") -> str:
    dialect = engine.dialect.name
    lines = [
        f"-- ========================================================",
        f"-- SQL Helper: Unified Data Type Optimization Script",
        f"-- Table: {table} | Dialect: {dialect.upper()}",
        f"-- ========================================================\n",
    ]

    for s in suggestions:
        lines.append(f"-- [{s['category']}] {s['column']}: {s['current_type']} -> {s['suggested_type']}")
        lines.append(f"-- Verification: {s.get('verification', 'Verified')}")
        lines.append(f"-- Reason: {s['reason']}")
        if s.get("est_total_saved_bytes", 0) > 0:
            lines.append(f"-- Estimated Space Saved: {s['formatted_savings']}")
        lines.append(f"{s['sql']}\n")

    return "\n".join(lines)


def generate_database_type_migration_script(
    engine: Engine,
    all_suggestions: dict[str, list[dict]],
    database: str = "",
    ai_audit_map: dict[str, dict[str, dict]] | None = None,
    ai_approved_only: bool = False,
) -> str:
    dialect = engine.dialect.name
    filtered_suggestions: dict[str, list[dict]] = {}

    for tbl, suggs in all_suggestions.items():
        tbl_key = tbl.lower()
        tbl_audit = (ai_audit_map or {}).get(tbl_key, {})
        valid_suggs = []
        for s in suggs:
            col_key = s["column"].lower()
            col_audit = tbl_audit.get(col_key, {})
            status = col_audit.get("status", "APPROVED")
            if ai_approved_only and status == "CAUTION":
                continue
            valid_suggs.append(s)
        if valid_suggs:
            filtered_suggestions[tbl] = valid_suggs

    mode_label = " (🛡️ AI-Approved Safe Migrations Only)" if ai_approved_only else " (📋 Complete Schema Profile)"

    lines = [
        f"-- ========================================================",
        f"-- SQL Helper: Database-Wide Data Type Optimization Script{mode_label}",
        f"-- Database: {database or 'Active Database'} | Dialect: {dialect.upper()}",
        f"-- Total Tables Included: {len(filtered_suggestions)}",
        f"-- ========================================================\n",
    ]

    for tbl, suggs in filtered_suggestions.items():
        tbl_key = tbl.lower()
        tbl_audit = (ai_audit_map or {}).get(tbl_key, {})
        lines.append(f"-- ── Table: `{tbl}` ({len(suggs)} Optimizations) ──────────────────────")
        for s in suggs:
            col_key = s["column"].lower()
            col_audit = tbl_audit.get(col_key, {})
            ai_status = col_audit.get("status", "")
            ai_note = f" [{ai_status}]" if ai_status else ""
            if col_audit.get("analysis"):
                lines.append(f"-- AI Semantic Note: {col_audit['analysis']}")
            lines.append(f"-- [{s['category']}]{ai_note} {s['column']}: {s['current_type']} -> {s['suggested_type']}")
            lines.append(f"{s['sql']}")
        lines.append("")

    return "\n".join(lines)


def _alter_col_sql(dialect: str, database: str, table: str, column: str, new_type: str, nullable: bool = True, is_auto_inc: bool = False) -> str:
    null_clause = " NULL" if nullable else " NOT NULL"
    auto_inc_clause = " AUTO_INCREMENT" if is_auto_inc and dialect == "mysql" else ""
    if dialect == "mysql":
        tbl = f"`{database}`.`{table}`" if database else f"`{table}`"
        return f"ALTER TABLE {tbl} MODIFY COLUMN `{column}` {new_type}{null_clause}{auto_inc_clause};"
    elif dialect == "postgresql":
        tbl = f'"{table}"'
        null_sql = f'ALTER TABLE {tbl} ALTER COLUMN "{column}" SET NOT NULL;' if not nullable else ""
        return f'ALTER TABLE {tbl} ALTER COLUMN "{column}" TYPE {new_type}; {null_sql}'.strip()
    else:  # sqlite
        return f'-- SQLite: Table rewrite required to alter column "{column}" to {new_type}{null_clause}'


