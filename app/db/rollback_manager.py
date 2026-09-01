"""
Index Rollback & Backup History Manager.
Tracks every index creation, drop, and optimization executed on the database.
Maintains exact inverse SQL statements with timestamps, table metadata, and descriptions
to allow 1-click safe rollback.
"""
from __future__ import annotations
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import yaml
from sqlalchemy import Engine
from app.db import schema_reader as sr

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def record_index_change(
    database: str,
    table: str,
    action_type: str,
    forward_sql: str,
    rollback_sql: str,
    description: str,
) -> dict[str, Any]:
    """
    Appends an executed index change to the persistent backup/rollback history.
    """
    entry = {
        "id": f"idx_rb_{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "database": database or "main",
        "table": table,
        "action_type": action_type,
        "forward_sql": forward_sql.strip(),
        "rollback_sql": rollback_sql.strip(),
        "description": description,
    }

    cfg = {}
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

    history = cfg.get("index_rollback_history", [])
    history.insert(0, entry)  # newest first
    # Keep up to 200 history items
    cfg["index_rollback_history"] = history[:200]

    try:
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass

    return entry


def get_index_change_history(database: str = "") -> list[dict[str, Any]]:
    """
    Retrieves the rollback history list, optionally filtered by database.
    """
    if not _CONFIG_PATH.exists():
        return []
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
            history = cfg.get("index_rollback_history", [])
            if database and database != "main":
                return [h for h in history if h.get("database") == database or not h.get("database")]
            return history
    except Exception:
        return []


def clear_index_change_history(database: str = "") -> None:
    """
    Clears the rollback history.
    """
    if not _CONFIG_PATH.exists():
        return
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
        if database:
            cfg["index_rollback_history"] = [
                h for h in cfg.get("index_rollback_history", [])
                if h.get("database") != database
            ]
        else:
            cfg["index_rollback_history"] = []
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass


def generate_consolidated_rollback_script(database: str = "") -> str:
    """
    Generates a consolidated SQL rollback script containing all recorded reverse DDL statements.
    """
    history = get_index_change_history(database)
    if not history:
        return "-- No index changes recorded in rollback history."

    lines = [
        "-- ========================================================",
        "-- SQL Helper: Comprehensive Index Rollback & Restore Script",
        f"-- Database: {database or 'All Databases'}",
        f"-- Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"-- Total Operations Recorded: {len(history)}",
        "-- ========================================================\n",
    ]

    for item in history:
        lines.append(f"-- [{item['timestamp']}] Table: `{item['table']}`")
        lines.append(f"-- Action Executed: {item['action_type']} — {item['description']}")
        lines.append(f"-- Forward Statement Was: {item['forward_sql']}")
        lines.append(f"{item['rollback_sql']}\n")

    return "\n".join(lines)


def infer_rollback_sql(
    dialect: str,
    database: str,
    forward_sql: str,
    table: str = "",
    columns: list[str] | None = None,
    index_name: str = "",
    is_unique: bool = False,
) -> tuple[str, str, str]:
    """
    Infers action_type, rollback_sql, and description from a forward index SQL statement.
    Returns: (action_type, rollback_sql, description)
    """
    sql_clean = forward_sql.strip().rstrip(";")
    sql_upper = sql_clean.upper()

    # 1. DROP INDEX statement -> Rollback is CREATE INDEX
    if "DROP INDEX" in sql_upper:
        action_type = "DROP INDEX"
        # Try to parse index_name and table
        if not index_name:
            m_idx = re.search(r"DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql_clean, re.IGNORECASE)
            index_name = m_idx.group(1) if m_idx else "idx_restored"

        if not table:
            m_tbl = re.search(r"\bON\s+[`\"]?(?:[a-zA-Z0-9_]+[`\"]?\.)?[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql_clean, re.IGNORECASE)
            table = m_tbl.group(1) if m_tbl else "target_table"

        cols_list = columns if columns else ["id"]
        uniq_clause = "UNIQUE " if is_unique else ""

        if dialect in ("mysql", "mariadb"):
            tbl_quoted = f"`{database}`.`{table}`" if database else f"`{table}`"
            cols_quoted = ", ".join(f"`{c}`" for c in cols_list)
            rollback_sql = f"CREATE {uniq_clause}INDEX `{index_name}` ON {tbl_quoted} ({cols_quoted});"
        elif dialect == "postgresql":
            tbl_quoted = f'"{table}"'
            cols_quoted = ", ".join(f'"{c}"' for c in cols_list)
            rollback_sql = f'CREATE {uniq_clause}INDEX CONCURRENTLY "{index_name}" ON {tbl_quoted} ({cols_quoted});'
        else:  # sqlite
            tbl_quoted = f'"{table}"'
            cols_quoted = ", ".join(f'"{c}"' for c in cols_list)
            rollback_sql = f'CREATE {uniq_clause}INDEX "{index_name}" ON {tbl_quoted} ({cols_quoted});'

        description = f"Restores dropped index `{index_name}` on table `{table}` ({', '.join(cols_list)})"
        return action_type, rollback_sql, description

    # 2. CREATE INDEX statement -> Rollback is DROP INDEX
    elif "CREATE" in sql_upper and "INDEX" in sql_upper:
        action_type = "CREATE INDEX"
        if not index_name:
            m_idx = re.search(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql_clean, re.IGNORECASE)
            index_name = m_idx.group(1) if m_idx else "idx_created"

        if not table:
            m_tbl = re.search(r"\bON\s+[`\"]?(?:[a-zA-Z0-9_]+[`\"]?\.)?[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql_clean, re.IGNORECASE)
            table = m_tbl.group(1) if m_tbl else "target_table"

        if dialect in ("mysql", "mariadb"):
            tbl_quoted = f"`{database}`.`{table}`" if database else f"`{table}`"
            rollback_sql = f"DROP INDEX `{index_name}` ON {tbl_quoted};"
        elif dialect == "postgresql":
            rollback_sql = f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}";'
        else:  # sqlite
            rollback_sql = f'DROP INDEX IF EXISTS "{index_name}";'

        description = f"Drops newly created index `{index_name}` on table `{table}`"
        return action_type, rollback_sql, description

    return "INDEX CHANGE", f"-- Rollback unknown for: {forward_sql}", f"Modifies index on `{table}`"
