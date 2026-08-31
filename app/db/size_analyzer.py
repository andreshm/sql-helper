"""
Database Size & Storage Analyzer.
Calculates storage footprints, data vs index allocations, bloat, and fragmentation.
Supports SQLite, MySQL/MariaDB, and PostgreSQL.
"""
from __future__ import annotations
import os
from pathlib import Path
from sqlalchemy import text, Engine
from app.db import schema_reader as sr


def format_bytes(num_bytes: int | float) -> str:
    """Format bytes into human-readable B, KB, MB, GB."""
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def get_database_storage_overview(engine: Engine, database: str = "") -> dict:
    """
    Returns full database-level and table-level storage breakdown.
    """
    dialect = engine.dialect.name
    tables_list = sr.list_tables(engine, database)

    if dialect == "sqlite":
        return _get_sqlite_storage(engine, tables_list)
    elif dialect == "mysql":
        return _get_mysql_storage(engine, database, tables_list)
    else:
        return _get_pg_storage(engine, tables_list)


def _get_sqlite_storage(engine: Engine, tables: list[str]) -> dict:
    with engine.connect() as conn:
        page_size_row = conn.execute(text("PRAGMA page_size")).fetchone()
        page_count_row = conn.execute(text("PRAGMA page_count")).fetchone()
        freelist_row = conn.execute(text("PRAGMA freelist_count")).fetchone()
        auto_vacuum_row = conn.execute(text("PRAGMA auto_vacuum")).fetchone()

    page_size = page_size_row[0] if page_size_row else 4096
    page_count = page_count_row[0] if page_count_row else 0
    freelist_count = freelist_row[0] if freelist_row else 0
    auto_vacuum_mode = {0: "NONE", 1: "FULL", 2: "INCREMENTAL"}.get(auto_vacuum_row[0] if auto_vacuum_row else 0, "NONE")

    total_file_size = page_size * page_count
    free_space_bytes = freelist_count * page_size

    # Inspect physical file if possible
    db_file_path = str(engine.url.database or "")
    wal_size_bytes = 0
    if db_file_path and db_file_path != ":memory:":
        try:
            p = Path(db_file_path)
            if p.exists():
                total_file_size = p.stat().st_size
            wal_p = Path(f"{db_file_path}-wal")
            if wal_p.exists():
                wal_size_bytes = wal_p.stat().st_size
        except Exception:
            pass

    table_records = []
    total_data_bytes = 0
    total_index_bytes = 0
    total_rows = 0

    for tbl in tables:
        stats = sr.get_table_stats(engine, "", tbl)
        indexes = sr.get_indexes(engine, "", tbl)
        columns = sr.get_columns(engine, "", tbl)
        rows_count = stats.get("approx_rows", 0)
        total_rows += rows_count

        # Estimate average row byte footprint
        col_type_bytes = 0
        for col in columns:
            t = col.get("type", "").upper()
            if "INT" in t:
                col_type_bytes += 4
            elif "FLOAT" in t or "DOUBLE" in t or "REAL" in t:
                col_type_bytes += 8
            elif "VARCHAR" in t or "TEXT" in t:
                col_type_bytes += 24
            elif "DATE" in t or "TIME" in t:
                col_type_bytes += 10
            else:
                col_type_bytes += 16
        
        avg_row = max(col_type_bytes, 16)
        tbl_data_bytes = rows_count * avg_row
        tbl_idx_bytes = len(indexes) * rows_count * 20
        tbl_total = tbl_data_bytes + tbl_idx_bytes

        total_data_bytes += tbl_data_bytes
        total_index_bytes += tbl_idx_bytes

        idx_to_data = (tbl_idx_bytes / tbl_data_bytes) if tbl_data_bytes > 0 else 0.0

        # Health status
        health = "Healthy"
        if idx_to_data > 1.5 and len(indexes) > 2:
            health = "Index Heavy"

        table_records.append({
            "table": tbl,
            "rows": rows_count,
            "avg_row_bytes": avg_row,
            "data_bytes": tbl_data_bytes,
            "index_bytes": tbl_idx_bytes,
            "total_bytes": tbl_total,
            "free_bytes": 0,
            "index_count": len(indexes),
            "index_to_data_ratio": round(idx_to_data, 2),
            "health": health,
        })

    # Adjust free space calculation
    active_used = total_data_bytes + total_index_bytes
    if total_file_size > active_used and free_space_bytes == 0:
        free_space_bytes = total_file_size - active_used

    # Check for table-level bloat flag if freelist exists
    if free_space_bytes > 0 and table_records:
        table_records[0]["free_bytes"] = free_space_bytes

    return {
        "dialect": "sqlite",
        "total_size_bytes": total_file_size,
        "data_size_bytes": total_data_bytes,
        "index_size_bytes": total_index_bytes,
        "free_space_bytes": free_space_bytes,
        "wal_size_bytes": wal_size_bytes,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "auto_vacuum_mode": auto_vacuum_mode,
        "table_count": len(tables),
        "total_rows": total_rows,
        "tables": table_records,
        "db_file_path": db_file_path,
    }


def _get_mysql_storage(engine: Engine, database: str, tables: list[str]) -> dict:
    table_records = []
    total_data_bytes = 0
    total_index_bytes = 0
    total_free_bytes = 0
    total_rows = 0

    with engine.connect() as conn:
        db_target = sr._resolve_mysql_db(conn, engine, database)
        rows = conn.execute(
            text(
                "SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, DATA_FREE, AVG_ROW_LENGTH "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'"
            ),
            {"db": db_target},
        ).fetchall()

    for r in rows:
        tbl = r[0]
        tbl_rows = r[1] or 0
        data_len = r[2] or 0
        idx_len = r[3] or 0
        free_len = r[4] or 0
        avg_row = r[5] or 0
        total_len = data_len + idx_len + free_len

        total_rows += tbl_rows
        total_data_bytes += data_len
        total_index_bytes += idx_len
        total_free_bytes += free_len

        indexes = sr.get_indexes(engine, db_target, tbl)
        idx_to_data = (idx_len / data_len) if data_len > 0 else 0.0

        health = "Healthy"
        if free_len > (total_len * 0.25) and free_len > 1024 * 1024:
            health = "Fragmented"
        elif idx_to_data > 1.5 and len(indexes) > 3:
            health = "Index Heavy"

        table_records.append({
            "table": tbl,
            "rows": tbl_rows,
            "avg_row_bytes": avg_row,
            "data_bytes": data_len,
            "index_bytes": idx_len,
            "total_bytes": total_len,
            "free_bytes": free_len,
            "index_count": len(indexes),
            "index_to_data_ratio": round(idx_to_data, 2),
            "health": health,
        })

    total_storage = total_data_bytes + total_index_bytes + total_free_bytes

    return {
        "dialect": "mysql",
        "total_size_bytes": total_storage,
        "data_size_bytes": total_data_bytes,
        "index_size_bytes": total_index_bytes,
        "free_space_bytes": total_free_bytes,
        "table_count": len(table_records),
        "total_rows": total_rows,
        "tables": table_records,
        "schema_name": db_target,
    }


def _get_pg_storage(engine: Engine, tables: list[str]) -> dict:
    table_records = []
    total_data_bytes = 0
    total_index_bytes = 0
    total_rows = 0

    with engine.connect() as conn:
        db_size_row = conn.execute(
            text("SELECT pg_database_size(current_database())")
        ).fetchone()
        db_total_bytes = db_size_row[0] if db_size_row else 0

        # Query all table stats in one go
        query = text(
            """
            SELECT 
                relname as table_name,
                n_live_tup as row_estimate,
                pg_table_size(relid) as data_bytes,
                pg_indexes_size(relid) as index_bytes,
                pg_total_relation_size(relid) as total_bytes,
                n_dead_tup as dead_tuples
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        )
        rows = conn.execute(query).fetchall()

    for r in rows:
        tbl = r[0]
        row_est = r[1] or 0
        data_b = r[2] or 0
        idx_b = r[3] or 0
        tot_b = r[4] or 0
        dead_tup = r[5] or 0

        total_rows += row_est
        total_data_bytes += data_b
        total_index_bytes += idx_b

        # Estimate dead space
        dead_bytes = int((data_b * (dead_tup / max(row_est + dead_tup, 1)))) if dead_tup > 0 else 0

        idx_to_data = (idx_b / data_b) if data_b > 0 else 0.0

        health = "Healthy"
        if dead_tup > 5000 and (dead_tup / max(row_est, 1)) > 0.2:
            health = "Bloated (Dead Tuples)"
        elif idx_to_data > 1.5:
            health = "Index Heavy"

        table_records.append({
            "table": tbl,
            "rows": row_est,
            "avg_row_bytes": (data_b // max(row_est, 1)),
            "data_bytes": data_b,
            "index_bytes": idx_b,
            "total_bytes": tot_b,
            "free_bytes": dead_bytes,
            "index_count": 0,
            "index_to_data_ratio": round(idx_to_data, 2),
            "health": health,
        })

    free_space = max(db_total_bytes - (total_data_bytes + total_index_bytes), 0)

    return {
        "dialect": "postgresql",
        "total_size_bytes": db_total_bytes,
        "data_size_bytes": total_data_bytes,
        "index_size_bytes": total_index_bytes,
        "free_space_bytes": free_space,
        "table_count": len(table_records),
        "total_rows": total_rows,
        "tables": table_records,
    }
