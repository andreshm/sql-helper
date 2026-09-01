"""
Data Type Optimizer & Storage Footprint Reduction Engine.
Profiles live database columns (sampling + deep verification) and generates single-pass,
backward-compatible DDL migrations with strict 3-tier safety risk classification.
"""
from __future__ import annotations
import re
from typing import Any
import pandas as pd
from sqlalchemy import text, Engine
from app.db import schema_reader as sr
from app.db.size_analyzer import format_bytes


def profile_table_columns(
    engine: Engine,
    database: str,
    table: str,
    sample_limit: int = 2000,
    deep_verify: bool = True,
) -> list[dict]:
    """
    Analyzes all columns in a table and produces complete, single-pass unified recommendations.
    Enforces strict risk classification:
      - 🟢 SAFE: Zero business growth risk (empty string sanitization, NOT NULL on fixed statuses, generous string shrinks).
      - 🟡 MAYBE / CHECK GROWTH: Requires user confirmation (string shrinking on names/text, integer downcasts on growing tables).
      - 🔴 HIGH RISK: ID/Sequence columns, code numbers, phone/zip/carrier numbers. Strictly unchecked by default.
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

        # Safety: Detect ID, Key, Sequence, or Foreign Key columns
        is_id_column = (
            is_pk or is_auto_inc or
            bool(re.search(r"(^id$|_id$|^id_|_id_|^fld.*id$|^pk_)", col_name, re.IGNORECASE)) or
            any(k in col_name.lower() for k in (
                "client_id", "customer_id", "user_id", "ticket_id", "order_id", "invoice_id",
                "record_id", "log_id", "account_id", "item_id", "emp_id", "employee_id",
                "member_id", "parent_id", "guid", "uuid", "tracking_no", "tracking_num",
                "order_no", "invoice_no", "sku", "barcode"
            ))
        )

        is_flag_or_counter = any(k in col_name.lower() for k in (
            "is_", "has_", "active", "enabled", "deleted", "flag", "status", "tier",
            "level", "priority", "count", "qty", "points", "attempts", "retry"
        ))

        is_text_content = any(k in col_name.lower() for k in (
            "name", "title", "desc", "addr", "comment", "note", "reason", "message",
            "url", "email", "body", "text", "company", "city", "state", "country", "street"
        ))

        is_phone_or_zip = any(k in col_name.lower() for k in (
            "phone", "tel", "mobile", "fax", "zip", "postal", "ssn", "pin", "barcode", "card", "tax_id"
        ))

        # Baseline metrics
        target_base_type = col_type
        target_bytes = current_bytes
        category = "Data Type Optimization"
        reason_parts = []
        pre_sql = ""
        verification_badge = "Sampled Profile"
        risk_level = "SAFE"
        risk_badge = "🟢 Safe & Confirmed"

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

            # Integer Downcasting Evaluation with Strict ID / Growth Protection
            if is_id_column:
                # NEVER downcast ID columns to TINYINT, SMALLINT, or MEDIUMINT!
                if "BIGINT" in col_type and 0 <= min_val and max_val <= 2000000000:
                    target_base_type = "INT UNSIGNED" if dialect == "mysql" else "INTEGER"
                    target_bytes = 4
                    category = "Integer Downcast (ID Capacity Protected)"
                    risk_level = "MAYBE"
                    risk_badge = "🟡 Check Growth"
                    reason_parts.append(f"ID sequence [{min_val:,} to {max_val:,}] fits safely in INT UNSIGNED (4.29B capacity). Verify long-term ID volume.")
            elif is_flag_or_counter:
                if 0 <= min_val and max_val <= 255:
                    target_base_type = "TINYINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 1 if dialect == "mysql" else 2
                    category = "Status Flag Optimization"
                    risk_level = "SAFE"
                    risk_badge = "🟢 Safe & Confirmed"
                    reason_parts.append(f"Status/flag column values [{min_val:,} to {max_val:,}] fit in 1-byte TINYINT.")
                elif 0 <= min_val and max_val <= 65535:
                    target_base_type = "SMALLINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 2
                    category = "Small Counter Optimization"
                    risk_level = "SAFE"
                    risk_badge = "🟢 Safe & Confirmed"
                    reason_parts.append(f"Counter values [{min_val:,} to {max_val:,}] fit in SMALLINT.")
            elif "BIGINT" in col_type or ("INT" in col_type and "TINY" not in col_type and "SMALL" not in col_type):
                if 0 <= min_val and max_val <= 255:
                    target_base_type = "TINYINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 1 if dialect == "mysql" else 2
                    category = "Integer Downcast"
                    risk_level = "MAYBE"
                    risk_badge = "🟡 Check Growth"
                    reason_parts.append(f"Values currently range from {min_val:,} to {max_val:,}. Verify future values will not exceed 255.")
                elif 0 <= min_val and max_val <= 65535:
                    target_base_type = "SMALLINT UNSIGNED" if dialect == "mysql" else "SMALLINT"
                    target_bytes = 2
                    category = "Integer Downcast"
                    risk_level = "MAYBE"
                    risk_badge = "🟡 Check Growth"
                    reason_parts.append(f"Values currently range from {min_val:,} to {max_val:,}. Verify future values will not exceed 65,535.")
                elif "BIGINT" in col_type and 0 <= min_val and max_val <= 4294967295:
                    target_base_type = "INT UNSIGNED" if dialect == "mysql" else "INTEGER"
                    target_bytes = 4
                    category = "Integer Downcast"
                    risk_level = "SAFE" if max_val < 50000000 else "MAYBE"
                    risk_badge = "🟢 Safe & Confirmed" if max_val < 50000000 else "🟡 Check Growth"
                    reason_parts.append(f"Values range from {min_val:,} to {max_val:,} (fits in 4-byte INT with 4.29B capacity).")

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

                            # 2A: Pure numeric check (High risk if code/tracking/phone)
                            if not is_id_column and max_len <= 18 and not is_phone_or_zip and non_digit_cnt == 0 and leading_zeros == 0 and max_len > 0:
                                min_eff = min(0, min_n) if (empty_count > 0 or null_count > 0) else min_n
                                max_eff = max(0, max_n)
                                if 0 <= min_eff and max_eff <= 255 and is_flag_or_counter:
                                    target_base_type, target_bytes = "TINYINT UNSIGNED", 1
                                    risk_level, risk_badge = "SAFE", "🟢 Safe & Confirmed"
                                elif 0 <= min_eff and max_eff <= 65535:
                                    target_base_type, target_bytes = "SMALLINT UNSIGNED", 2
                                    risk_level, risk_badge = "MAYBE", "🟡 Check Growth"
                                elif 0 <= min_eff and max_eff <= 4294967295:
                                    target_base_type, target_bytes = "INT UNSIGNED", 4
                                    risk_level, risk_badge = "MAYBE", "🟡 Check Growth"
                                else:
                                    target_base_type, target_bytes = "BIGINT UNSIGNED", 8
                                    risk_level, risk_badge = "MAYBE", "🟡 Check Growth"

                                category = "String to Integer Conversion"
                                reason_parts.append(f"Contains 100% numeric digits (Min: {min_n:,}, Max: {max_n:,}). Converting to native {target_base_type}.")

                except Exception:
                    pass
            else:
                null_count = sample_size - len(series)
                empty_count = int((str_s == "").sum())

            # 2B: UUID format check
            if target_base_type == col_type and max_len == 36 and min_len == 36 and str_s.str.contains(r"^[0-9a-fA-F-]{36}$").all():
                target_base_type = "UUID" if dialect == "postgresql" else "CHAR(36)"
                target_bytes = 36
                category = "UUID Optimization"
                risk_level = "SAFE"
                risk_badge = "🟢 Safe & Confirmed"
                reason_parts.append("Matches exact 36-char UUID format.")

            # 2C: Oversized string shrink check with Generous Headroom
            if target_base_type == col_type and declared_len >= 100 and max_len <= 35:
                # Generous safety headroom: at least 2x max length or 64 bytes
                safe_len = max(max_len * 2, 64)
                safe_len = ((safe_len + 15) // 16) * 16  # standard 64, 80, 96, 128
                if safe_len < declared_len:
                    target_base_type = f"VARCHAR({safe_len})"
                    target_bytes = max(int((declared_len - safe_len) * 0.3), 2)
                    category = "Oversized String Shrink"
                    risk_level = "MAYBE" if is_text_content else "SAFE"
                    risk_badge = "🟡 Check Growth" if is_text_content else "🟢 Safe & Confirmed"
                    reason_parts.append(f"Max observed length is {max_len} chars. Resized to VARCHAR({safe_len}) with {safe_len - max_len} chars safety headroom.")

        # ===================================================================
        # 3. Currency / Float Precision Optimization
        # ===================================================================
        elif any(t in col_type for t in ("FLOAT", "DOUBLE", "REAL")):
            if any(k in col_name.lower() for k in ("price", "amount", "cost", "total", "fee", "tax", "balance", "salary", "rate")):
                target_base_type = "DECIMAL(12, 2)" if dialect != "sqlite" else "DECIMAL"
                target_bytes = 6
                category = "Financial Decimal Accuracy"
                risk_level = "SAFE"
                risk_badge = "🟢 Safe & Confirmed"
                reason_parts.append(f"Represents financial currency data (replaces imprecise {col_type}).")

        # ===================================================================
        # 4. Compute Optimal Unified Nullability & Sanitization (Single Pass!)
        # ===================================================================
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
                saved_per_row = 1
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

            # Assign category and risk if it was purely sanitization
            if not is_type_changed:
                category = "Sanitize Empty Strings to NULL" if is_sanitized else "Tighten Nullability"
                risk_level = "SAFE"
                risk_badge = "🟢 Safe & Confirmed"

            # Assign default check state: Only SAFE is checked by default!
            default_checked = (risk_level == "SAFE")

            suggestions.append({
                "table": table,
                "column": col_name,
                "category": category,
                "risk_level": risk_level,
                "risk_badge": risk_badge,
                "default_checked": default_checked,
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
    if "INT" in t or "INTEGER" in t:
        return 4
    if "DECIMAL" in t or "NUMERIC" in t:
        return 6
    if "FLOAT" in t:
        return 4
    if "DOUBLE" in t or "REAL" in t:
        return 8
    if "UUID" in t:
        return 16
    if "VARCHAR" in t or "CHAR" in t:
        m = re.search(r"\((\d+)\)", t)
        if m:
            return min(int(m.group(1)), 255)
        return 255
    if "TEXT" in t or "BLOB" in t:
        return 255
    return 8


def _alter_col_sql(
    dialect: str,
    database: str,
    table: str,
    column: str,
    target_type: str,
    nullable: bool = True,
    default: str = "",
    auto_increment: bool = False,
) -> str:
    """Generates the dialect-specific ALTER TABLE column modification SQL."""
    tbl_quoted = f"`{database}`.`{table}`" if dialect == "mysql" and database else (f"`{table}`" if dialect == "mysql" else f'"{table}"')
    col_quoted = f"`{column}`" if dialect == "mysql" else f'"{column}"'
    null_clause = "NULL" if nullable else "NOT NULL"
    auto_inc_clause = " AUTO_INCREMENT" if auto_increment and dialect == "mysql" else ""
    d_clause = f" {default}" if default else ""

    if dialect == "mysql":
        return f"ALTER TABLE {tbl_quoted} MODIFY COLUMN {col_quoted} {target_type} {null_clause}{auto_inc_clause}{d_clause};"
    elif dialect == "postgresql":
        not_null_part = f' ALTER TABLE {tbl_quoted} ALTER COLUMN {col_quoted} SET NOT NULL;' if not nullable else ""
        return f'ALTER TABLE {tbl_quoted} ALTER COLUMN {col_quoted} TYPE {target_type};{not_null_part}'
    else:  # sqlite
        return f'-- SQLite: Rebuild required for "{column}" to {target_type}'



def scan_all_tables_for_types(
    engine: Engine,
    database: str,
    sample_limit: int = 1500,
    deep_verify: bool = True,
) -> dict[str, list[dict]]:
    """Scans all tables in the target database for single-pass unified type recommendations."""
    tables = sr.list_tables(engine, database)
    all_suggestions: dict[str, list[dict]] = {}

    for tbl in tables:
        try:
            suggs = profile_table_columns(engine, database, tbl, sample_limit=sample_limit, deep_verify=deep_verify)
            if suggs:
                all_suggestions[tbl] = suggs
        except Exception:
            continue

    return all_suggestions


def generate_database_type_migration_script(
    database_suggestions: dict[str, list[dict]],
    dialect: str = "mysql",
    database: str = "",
    ai_audit_map: dict[str, dict] | None = None,
    ai_approved_only: bool = False,
) -> str:
    """Generates a complete batch DDL SQL migration script for all recommended data type optimizations."""
    total_modifications = sum(len(s) for s in database_suggestions.values())
    if total_modifications == 0:
        return "-- No data type optimizations found."

    lines = [
        f"-- ========================================================",
        f"-- SQL Helper: Database-Wide Data Type Migration Script",
        f"-- Dialect: {dialect.upper()} | Target Database: {database or 'Active Schema'}",
        f"-- Generated Single-Pass Optimizations & Sanitizations",
        f"-- ========================================================",
        f"",
        f"SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO';" if dialect == "mysql" else "",
        f"",
    ]

    for tbl, suggs in database_suggestions.items():
        tbl_lines = []
        for s in suggs:
            col_key = f"{tbl}.{s['column']}"
            ai_info = ai_audit_map.get(col_key, {}) if ai_audit_map else {}
            ai_status = ai_info.get("status", "APPROVED")

            if ai_approved_only and ai_status == "CAUTION":
                continue

            tbl_lines.append(f"-- [{s['risk_badge']}] Column `{s['column']}`: {s['current_type']} -> {s['suggested_type']}")
            tbl_lines.append(f"-- Reason: {s['reason']}")
            if ai_info and ai_info.get("analysis"):
                tbl_lines.append(f"-- AI Audit ({ai_status}): {ai_info['analysis']}")
            tbl_lines.append(f"{s['sql']}\n")

        if tbl_lines:
            lines.append(f"-- --------------------------------------------------------")
            lines.append(f"-- Table: `{tbl}`")
            lines.append(f"-- --------------------------------------------------------")
            lines.extend(tbl_lines)

    lines.append(f"SET SQL_MODE=@OLD_SQL_MODE;" if dialect == "mysql" else "")
    return "\n".join([l for l in lines if l is not None])
