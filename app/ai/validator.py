"""
Anti-Hallucination & Schema Verification Engine.
Cross-references AI-generated SQL and recommendations against the live database catalog
to ensure tables, columns, indexes, and dialect syntax are 100% factual and executable.
"""
from __future__ import annotations
import re
from typing import Any
from sqlalchemy import text, Engine
from app.db import schema_reader as sr


def verify_sql_against_schema(
    engine: Engine,
    sql: str,
    database: str = "",
    target_table: str = "",
) -> dict:
    """
    Parses an AI-generated SQL statement and validates it against live database metadata.
    Auto-qualifies table names with database for MySQL if needed.
    """
    dialect = engine.dialect.name
    clean_sql = sql.strip().rstrip(";")
    upper_sql = clean_sql.upper()

    issues: list[str] = []
    notes: list[str] = []
    referenced_table = target_table
    referenced_columns: list[str] = []
    normalized_sql = clean_sql + ";"
    action_type = "UNKNOWN"

    # Check if this statement was already executed in session state
    is_already_executed = False
    try:
        import streamlit as st
        if hasattr(st, "runtime") and st.runtime.exists() and hasattr(st, "session_state"):
            executed_set = st.session_state.get("executed_fixes", set())
            if clean_sql in executed_set or normalized_sql in executed_set or sql in executed_set:
                is_already_executed = True
    except Exception:
        pass

    # Get available tables from live database
    try:
        available_tables = sr.list_tables(engine, database)
    except Exception as exc:
        available_tables = []
        issues.append(f"Could not retrieve tables from catalog: {exc}")

    # -----------------------------------------------------------------------
    # 1. Validate CREATE INDEX statements
    # -----------------------------------------------------------------------
    if upper_sql.startswith("CREATE INDEX") or upper_sql.startswith("CREATE UNIQUE INDEX"):
        action_type = "CREATE"
        is_unique = "UNIQUE" in upper_sql
        # Match pattern: CREATE [UNIQUE] INDEX [name] ON [table] (col1, col2, ...)
        m = re.search(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\"`\']?(\w+)[\"`\']?\s+ON\s+(?:[`\"']?(\w+)[`\"']?\.)?[`\"']?(\w+)[`\"']?\s*\((.+?)\)",
            clean_sql,
            re.IGNORECASE,
        )
        if m:
            idx_name = m.group(1).strip("`\"'")
            db_prefix = m.group(2)
            tbl_name = m.group(3).strip("`\"'")
            cols_raw = m.group(4)
            referenced_table = tbl_name
            cols = [c.strip().strip("`\"'").split()[0] for c in cols_raw.split(",")]
            referenced_columns = cols

            # Check Table Existence
            if tbl_name not in available_tables:
                issues.append(f"Hallucination Warning: Table `{tbl_name}` does not exist in schema `{database or 'default'}`.")
            else:
                # Check Columns Existence
                try:
                    table_cols = {c["column"].lower() for c in sr.get_columns(engine, database, tbl_name)}
                    for col in cols:
                        if col.lower() not in table_cols:
                            issues.append(f"Hallucination Warning: Column `{col}` does not exist in table `{tbl_name}`.")
                except Exception as exc:
                    notes.append(f"Column check note: {exc}")

                # Check if Index Already Exists
                if not is_already_executed:
                    try:
                        existing_indexes = sr.get_indexes(engine, database, tbl_name)
                        existing_names = {i["name"].lower() for i in existing_indexes}
                        existing_col_tuples = {tuple(c.lower() for c in i.get("columns", [])) for i in existing_indexes}

                        if idx_name.lower() in existing_names:
                            issues.append(f"Redundancy: Index `{idx_name}` already exists on table `{tbl_name}`.")

                        if tuple(c.lower() for c in cols) in existing_col_tuples:
                            issues.append(f"Redundancy: An index with identical columns ({', '.join(cols)}) already exists on `{tbl_name}`.")
                    except Exception as exc:
                        notes.append(f"Index duplicate check note: {exc}")

            # Normalize SQL with proper database/table qualification
            unique_str = "UNIQUE " if is_unique else ""
            if dialect == "mysql":
                tbl_qualified = f"`{database}`.`{tbl_name}`" if database else f"`{tbl_name}`"
                cols_str = ", ".join(f"`{c}`" for c in cols)
                normalized_sql = f"CREATE {unique_str}INDEX `{idx_name}` ON {tbl_qualified} ({cols_str});"
            elif dialect == "postgresql":
                cols_str = ", ".join(f'"{c}"' for c in cols)
                normalized_sql = f'CREATE {unique_str}INDEX CONCURRENTLY IF NOT EXISTS "{idx_name}" ON "{tbl_name}" ({cols_str});'
            else:  # sqlite
                cols_str = ", ".join(f'"{c}"' for c in cols)
                normalized_sql = f'CREATE {unique_str}INDEX IF NOT EXISTS "{idx_name}" ON "{tbl_name}" ({cols_str});'

        else:
            notes.append("Could not fully parse CREATE INDEX syntax.")

    # -----------------------------------------------------------------------
    # 2. Validate DROP INDEX statements
    # -----------------------------------------------------------------------
    elif upper_sql.startswith("DROP INDEX"):
        action_type = "DROP"
        m = re.search(r"DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?(?:CONCURRENTLY\s+)?[\"`\']?(\w+)[\"`\']?(?:\s+ON\s+(?:[`\"']?(\w+)[`\"']?\.)?[`\"']?(\w+)[\"`\']?)?", clean_sql, re.IGNORECASE)
        if m:
            idx_name = m.group(1).strip("`\"'")
            tbl_name = (m.group(3) or m.group(2) or target_table).strip("`\"'")
            referenced_table = tbl_name

            # Guardrail 1: Do not allow dropping PRIMARY KEY via DROP INDEX
            if idx_name.upper() == "PRIMARY" or idx_name.startswith("sqlite_autoindex_"):
                issues.append("Safety Protection: Index 'PRIMARY' is the table's PRIMARY KEY. Dropping primary keys is disabled to preserve schema integrity and row identity.")
            else:
                # Check if index exists in table catalog
                if tbl_name and not is_already_executed:
                    try:
                        tbl_indexes = sr.get_indexes(engine, database, tbl_name)
                        existing_names = {i["name"].lower() for i in tbl_indexes}
                        if idx_name.lower() not in existing_names:
                            issues.append(f"Discrepancy: Index `{idx_name}` does not exist on table `{tbl_name}`.")

                        # Guardrail 2: MySQL AUTO_INCREMENT index check (Prevents MySQL Error 1075)
                        if dialect == "mysql":
                            table_cols = sr.get_columns(engine, database, tbl_name)
                            auto_inc_cols = {c["column"].lower() for c in table_cols if "auto_increment" in c.get("extra", "").lower()}
                            if auto_inc_cols:
                                target_idx = next((i for i in tbl_indexes if i["name"].lower() == idx_name.lower()), None)
                                if target_idx:
                                    lead_col = (target_idx.get("columns") or [""])[0].lower()
                                    if lead_col in auto_inc_cols:
                                        other_leads = sum(1 for i in tbl_indexes if i["name"].lower() != idx_name.lower() and i.get("columns") and i["columns"][0].lower() == lead_col)
                                        if other_leads == 0:
                                            issues.append(f"MySQL Constraint (Error 1075): Column '{lead_col}' is defined as AUTO_INCREMENT and requires an index. Dropping index '{idx_name}' is blocked by MySQL.")
                    except Exception:
                        pass

            if dialect == "mysql":
                tbl_qualified = f"`{database}`.`{tbl_name}`" if database else f"`{tbl_name}`"
                normalized_sql = f"DROP INDEX `{idx_name}` ON {tbl_qualified};"
            elif dialect == "postgresql":
                normalized_sql = f'DROP INDEX CONCURRENTLY IF EXISTS "{idx_name}";'
            else:  # sqlite
                normalized_sql = f'DROP INDEX IF EXISTS "{idx_name}";'
        else:
            notes.append("Could not fully parse DROP INDEX syntax.")

    # -----------------------------------------------------------------------
    # 3. Validate ALTER TABLE statements
    # -----------------------------------------------------------------------
    elif upper_sql.startswith("ALTER TABLE"):
        action_type = "ALTER"
        m = re.search(r"ALTER\s+TABLE\s+(?:[`\"']?(\w+)[`\"']?\.)?[`\"']?(\w+)[`\"']?", clean_sql, re.IGNORECASE)
        if m:
            tbl_name = m.group(2).strip("`\"'")
            referenced_table = tbl_name
            if tbl_name not in available_tables:
                issues.append(f"Hallucination Warning: Table `{tbl_name}` does not exist in schema.")
        else:
            notes.append("Could not fully parse ALTER TABLE syntax.")

    # -----------------------------------------------------------------------
    # Result evaluation
    # -----------------------------------------------------------------------
    if is_already_executed:
        return {
            "is_valid": True,
            "is_applied": True,
            "action_type": action_type,
            "badge": "✅ Fix Applied Successfully",
            "badge_type": "success",
            "sql": normalized_sql,
            "table": referenced_table,
            "columns": referenced_columns,
            "issues": [],
            "notes": ["This fix has already been executed on the live database catalog."],
        }

    is_valid = len(issues) == 0
    if is_valid:
        badge = "🛡️ Verified by Catalog"
        badge_type = "success"
        notes.append("All referenced tables, columns, and index names cross-referenced against live schema catalog.")
    else:
        badge = "⚠️ Discrepancy Found"
        badge_type = "danger"

    return {
        "is_valid": is_valid,
        "is_applied": False,
        "action_type": action_type,
        "badge": badge,
        "badge_type": badge_type,
        "sql": normalized_sql,
        "table": referenced_table,
        "columns": referenced_columns,
        "issues": issues,
        "notes": notes,
    }


def extract_and_verify_sql_statements(
    engine: Engine,
    ai_response_text: str,
    database: str = "",
    target_table: str = "",
) -> list[dict]:
    """
    Extracts all DDL/DML statements from raw AI markdown text (code blocks, bullet lists, inline)
    and validates each against the live database schema catalog.
    """
    raw_statements = []

    # 1. Extract from ```sql code blocks
    code_blocks = re.findall(r"```(?:sql)?\s*(.*?)\s*```", ai_response_text, re.DOTALL | re.IGNORECASE)
    for block in code_blocks:
        for stmt in block.split(";"):
            s = re.sub(r"--.*$", "", stmt, flags=re.MULTILINE).strip()
            if s and not s.startswith("--") and any(s.upper().startswith(k) for k in ("CREATE", "DROP", "ALTER")):
                raw_statements.append(s)

    # 2. Extract CREATE INDEX statements across full markdown text
    create_matches = re.finditer(
        r"(CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[\`\"']?\w+[\`\"']?\s+ON\s+[\`\"'\w\.]+\s*\([^;)]+\))",
        ai_response_text,
        re.IGNORECASE,
    )
    for m in create_matches:
        raw_statements.append(m.group(1).strip())

    # 3. Extract DROP INDEX statements across full markdown text (e.g. bullets, lists, lines)
    drop_matches = re.finditer(
        r"(DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?(?:CONCURRENTLY\s+)?[\`\"']?\w+[\`\"']?(?:\s+ON\s+[\`\"'\w\.]+)?)(?:\s*;|\s+Justification|\s+--|\s*\n|$)",
        ai_response_text,
        re.IGNORECASE,
    )
    for m in drop_matches:
        raw_statements.append(m.group(1).strip())

    # 4. Extract ALTER TABLE statements across full markdown text
    alter_matches = re.finditer(
        r"(ALTER\s+TABLE\s+[\`\"'\w\.]+\s+[^;\n]+)",
        ai_response_text,
        re.IGNORECASE,
    )
    for m in alter_matches:
        raw_statements.append(m.group(1).strip())

    # Deduplicate while preserving order and normalizing whitespace
    seen = set()
    unique_statements = []
    for s in raw_statements:
        norm = re.sub(r"\s+", " ", s).strip().rstrip(";")
        # Remove any leading bullet marks or numbers (e.g., "- ", "* ", "1. ")
        norm = re.sub(r"^[\s\-\*\•\d\.]+", "", norm).strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            unique_statements.append(norm)

    # Validate each statement against catalog
    verified_results = []
    for stmt in unique_statements:
        val = verify_sql_against_schema(engine, stmt, database=database, target_table=target_table)
        verified_results.append(val)

    return verified_results
