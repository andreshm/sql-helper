"""
Database Maintenance, Compaction and Resizing Engine.
Executes VACUUM, OPTIMIZE, REINDEX, and defragmentation operations
while tracking before/after storage sizes and reclaimed bytes.
"""
from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import text, Engine
from app.db.size_analyzer import get_database_storage_overview, format_bytes


def run_maintenance_action(
    engine: Engine,
    action: str,
    table: str = "",
    database: str = "",
) -> dict:
    """
    Executes a compaction/resizing maintenance task and returns the before/after delta.
    """
    dialect = engine.dialect.name
    overview_before = get_database_storage_overview(engine, database)
    size_before = overview_before["total_size_bytes"]

    # Table specific before bytes if applicable
    tbl_before = next((t["total_bytes"] for t in overview_before.get("tables", []) if t["table"] == table), None)

    sql_executed = ""
    error = ""

    try:
        if dialect == "sqlite":
            if action == "vacuum":
                sql_executed = "VACUUM;"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("VACUUM;"))

            elif action == "wal_checkpoint":
                sql_executed = "PRAGMA wal_checkpoint(TRUNCATE);"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE);"))

            elif action == "analyze":
                sql_executed = "ANALYZE; PRAGMA optimize;"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("ANALYZE;"))
                    conn.execute(text("PRAGMA optimize;"))

            elif action == "reindex":
                sql_executed = f'REINDEX "{table}";' if table else "REINDEX;"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(sql_executed))

        elif dialect == "mysql":
            tbl_quoted = f"`{database}`.`{table}`" if database else f"`{table}`"
            if action == "optimize_table":
                sql_executed = f"OPTIMIZE TABLE {tbl_quoted};"
                with engine.connect() as conn:
                    conn.execute(text(sql_executed))

            elif action == "rebuild_table":
                sql_executed = f"ALTER TABLE {tbl_quoted} ENGINE=InnoDB;"
                with engine.connect() as conn:
                    conn.execute(text(sql_executed))

            elif action == "analyze_table":
                sql_executed = f"ANALYZE TABLE {tbl_quoted};"
                with engine.connect() as conn:
                    conn.execute(text(sql_executed))

        else:  # postgresql
            tbl_quoted = f'"{table}"'
            if action == "vacuum_table":
                sql_executed = f"VACUUM (ANALYZE) {tbl_quoted};"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(sql_executed))

            elif action == "vacuum_full":
                sql_executed = f"VACUUM FULL {tbl_quoted};"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(sql_executed))

            elif action == "reindex_table":
                sql_executed = f"REINDEX TABLE {tbl_quoted};"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(sql_executed))

            elif action == "analyze_table":
                sql_executed = f"ANALYZE {tbl_quoted};"
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(sql_executed))

    except Exception as exc:
        error = str(exc)

    overview_after = get_database_storage_overview(engine, database)
    size_after = overview_after["total_size_bytes"]
    tbl_after = next((t["total_bytes"] for t in overview_after.get("tables", []) if t["table"] == table), None)

    # Calculate reclaimed space
    if tbl_before is not None and tbl_after is not None and table:
        reclaimed_bytes = max(tbl_before - tbl_after, 0)
        reclaimed_percent = (reclaimed_bytes / tbl_before * 100) if tbl_before > 0 else 0.0
    else:
        reclaimed_bytes = max(size_before - size_after, 0)
        reclaimed_percent = (reclaimed_bytes / size_before * 100) if size_before > 0 else 0.0

    return {
        "success": not bool(error),
        "error": error,
        "action": action,
        "table": table,
        "sql_executed": sql_executed,
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "reclaimed_bytes": reclaimed_bytes,
        "reclaimed_percent": round(reclaimed_percent, 1),
        "formatted_reclaimed": format_bytes(reclaimed_bytes),
    }


def generate_maintenance_script(engine: Engine, tables: list[str], database: str = "") -> str:
    """
    Generates a full SQL maintenance and compaction script.
    """
    dialect = engine.dialect.name
    lines = [f"-- ========================================================",
             f"-- SQL Helper: Database Maintenance & Compaction Script",
             f"-- Dialect: {dialect.upper()}",
             f"-- ========================================================\n"]

    if dialect == "sqlite":
        lines.append("-- 1. Flush write-ahead log (shrink WAL to 0)")
        lines.append("PRAGMA wal_checkpoint(TRUNCATE);\n")
        lines.append("-- 2. Update query planner statistics and histogram")
        lines.append("ANALYZE;")
        lines.append("PRAGMA optimize;\n")
        lines.append("-- 3. Rebuild B-tree indexes")
        lines.append("REINDEX;\n")
        lines.append("-- 4. Reclaim all deleted freelist space and compact database file")
        lines.append("VACUUM;")

    elif dialect == "mysql":
        lines.append(f"-- Database: `{database}`\n")
        for tbl in tables:
            tbl_quoted = f"`{database}`.`{tbl}`" if database else f"`{tbl}`"
            lines.append(f"-- Defragment and rebuild `{tbl}`")
            lines.append(f"OPTIMIZE TABLE {tbl_quoted};")
            lines.append(f"ANALYZE TABLE {tbl_quoted};\n")

    else:  # postgresql
        for tbl in tables:
            lines.append(f"-- Clean dead tuples and update statistics for {tbl}")
            lines.append(f'VACUUM (ANALYZE) "{tbl}";')
            lines.append(f'REINDEX TABLE "{tbl}";\n')

    return "\n".join(lines)
