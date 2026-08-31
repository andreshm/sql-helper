from __future__ import annotations
import re
from typing import Any
from sqlalchemy import text, Engine


# ---------------------------------------------------------------------------
# Databases / schemas
# ---------------------------------------------------------------------------

def list_databases(engine: Engine) -> list[str]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            return ["main"]
        elif dialect == "mysql":
            rows = conn.execute(text("SHOW DATABASES")).fetchall()
            skip = {"information_schema", "performance_schema", "sys", "mysql"}
            return [r[0] for r in rows if r[0] not in skip]
        else:
            rows = conn.execute(
                text("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
            ).fetchall()
            return [r[0] for r in rows]


def _resolve_mysql_db(conn, engine: Engine, database: str = "") -> str:
    if database and database != "main":
        return database
    if engine.url.database:
        return engine.url.database
    try:
        cur_row = conn.execute(text("SELECT DATABASE()")).fetchone()
        if cur_row and cur_row[0]:
            return cur_row[0]
    except Exception:
        pass
    dbs = list_databases(engine)
    return dbs[0] if dbs else ""


# ---------------------------------------------------------------------------
# Tables / views
# ---------------------------------------------------------------------------

def list_tables(engine: Engine, database: str = "") -> list[str]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            ).fetchall()
            return [r[0] for r in rows]
        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text("SELECT TABLE_NAME FROM information_schema.TABLES "
                     "WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"),
                {"db": db_target},
            ).fetchall()
            return [r[0] for r in rows]
        else:
            rows = conn.execute(
                text("SELECT table_name FROM information_schema.tables "
                     "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")
            ).fetchall()
            return [r[0] for r in rows]


def list_views(engine: Engine, database: str = "") -> list[str]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")
            ).fetchall()
            return [r[0] for r in rows]
        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text("SELECT TABLE_NAME FROM information_schema.VIEWS "
                     "WHERE TABLE_SCHEMA = :db ORDER BY TABLE_NAME"),
                {"db": db_target},
            ).fetchall()
            return [r[0] for r in rows]
        else:
            rows = conn.execute(
                text("SELECT table_name FROM information_schema.views "
                     "WHERE table_schema = 'public' ORDER BY table_name")
            ).fetchall()
            return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns(engine: Engine, database: str, table: str) -> list[dict]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            tbl_clean = table.replace('"', '""')
            rows = conn.execute(text(f'PRAGMA table_info("{tbl_clean}")')).fetchall()
            cols = []
            for r in rows:
                cols.append({
                    "column": r[1],
                    "type": r[2] or "TEXT",
                    "nullable": r[3] == 0,
                    "default": r[4],
                    "key": "PRI" if r[5] > 0 else "",
                    "extra": "auto_increment" if (r[5] == 1 and "INT" in (r[2] or "").upper()) else "",
                    "comment": "",
                })
            return cols

        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                    "COLUMN_KEY, EXTRA, COLUMN_COMMENT "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl "
                    "ORDER BY ORDINAL_POSITION"
                ),
                {"db": db_target, "tbl": table},
            ).fetchall()
            return [
                {
                    "column": r[0],
                    "type": r[1],
                    "nullable": r[2] == "YES",
                    "default": r[3],
                    "key": r[4],
                    "extra": r[5],
                    "comment": r[6],
                }
                for r in rows
            ]
        else:
            rows = conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default, "
                    "character_maximum_length, numeric_precision "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :tbl "
                    "ORDER BY ordinal_position"
                ),
                {"tbl": table},
            ).fetchall()
            pk_cols = _get_pg_pk_columns(conn, table)
            return [
                {
                    "column": r[0],
                    "type": r[1],
                    "nullable": r[2] == "YES",
                    "default": r[3],
                    "key": "PRI" if r[0] in pk_cols else "",
                    "extra": "",
                    "comment": "",
                }
                for r in rows
            ]


def _get_pg_pk_columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            "  AND tc.table_schema = kcu.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = :tbl"
        ),
        {"tbl": table},
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

def get_indexes(engine: Engine, database: str, table: str) -> list[dict]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            tbl_clean = table.replace('"', '""')
            idx_rows = conn.execute(text(f'PRAGMA index_list("{tbl_clean}")')).fetchall()
            indexes = []
            for r in idx_rows:
                idx_name = r[1]
                is_unique = bool(r[2])
                idx_clean = idx_name.replace('"', '""')
                col_rows = conn.execute(text(f'PRAGMA index_info("{idx_clean}")')).fetchall()
                col_names = [c[2] for c in col_rows if c[2]]
                indexes.append({
                    "name": idx_name,
                    "unique": is_unique,
                    "columns": col_names,
                    "type": "BTREE",
                    "definition": f"CREATE {'UNIQUE ' if is_unique else ''}INDEX {idx_name} ON {table} ({', '.join(col_names)})",
                })
            return indexes

        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text("SHOW INDEX FROM `" + table + "` FROM `" + db_target + "`")
            ).fetchall()
            cols = list(rows[0]._fields) if rows else []
            results: dict[str, dict] = {}
            for r in rows:
                rd = dict(zip(cols, r))
                name = rd["Key_name"]
                if name not in results:
                    results[name] = {
                        "name": name,
                        "unique": rd["Non_unique"] == 0,
                        "columns": [],
                        "type": rd.get("Index_type", "BTREE"),
                    }
                results[name]["columns"].append(rd["Column_name"])
            return list(results.values())
        else:
            rows = conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = :tbl ORDER BY indexname"
                ),
                {"tbl": table},
            ).fetchall()
            return [
                {
                    "name": r[0],
                    "unique": "UNIQUE" in r[1].upper(),
                    "columns": _extract_pg_index_columns(r[1]),
                    "type": "BTREE",
                    "definition": r[1],
                }
                for r in rows
            ]


def _extract_pg_index_columns(definition: str) -> list[str]:
    m = re.search(r"\((.+)\)$", definition.strip())
    if m:
        return [c.strip().strip('"') for c in m.group(1).split(",")]
    return []


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

def get_foreign_keys(engine: Engine, database: str, table: str) -> list[dict]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            tbl_clean = table.replace('"', '""')
            rows = conn.execute(text(f'PRAGMA foreign_key_list("{tbl_clean}")')).fetchall()
            return [
                {
                    "id": r[0],
                    "column": r[3],
                    "ref_table": r[2],
                    "ref_column": r[4],
                    "on_update": r[5],
                    "on_delete": r[6],
                }
                for r in rows
            ]
        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text(
                    "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, CONSTRAINT_NAME "
                    "FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl AND REFERENCED_TABLE_NAME IS NOT NULL"
                ),
                {"db": db_target, "tbl": table},
            ).fetchall()
            return [
                {
                    "column": r[0],
                    "ref_table": r[1],
                    "ref_column": r[2],
                    "constraint": r[3],
                }
                for r in rows
            ]
        else:
            rows = conn.execute(
                text(
                    "SELECT kcu.column_name, ccu.table_name, ccu.column_name, tc.constraint_name "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
                    "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = :tbl"
                ),
                {"tbl": table},
            ).fetchall()
            return [
                {
                    "column": r[0],
                    "ref_table": r[1],
                    "ref_column": r[2],
                    "constraint": r[3],
                }
                for r in rows
            ]


# ---------------------------------------------------------------------------
# Procedures & triggers
# ---------------------------------------------------------------------------

def list_procedures(engine: Engine, database: str = "") -> list[str]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text(
                    "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
                    "WHERE ROUTINE_SCHEMA = :db AND ROUTINE_TYPE = 'PROCEDURE'"
                ),
                {"db": db_target},
            ).fetchall()
            return [r[0] for r in rows]
        elif dialect == "postgresql":
            rows = conn.execute(
                text("SELECT proname FROM pg_proc JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid WHERE nspname = 'public'")
            ).fetchall()
            return [r[0] for r in rows]
        return []


def get_procedure_body(engine: Engine, database: str, proc_name: str) -> str:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            row = conn.execute(
                text(
                    "SELECT ROUTINE_DEFINITION FROM information_schema.ROUTINES "
                    "WHERE ROUTINE_SCHEMA = :db AND ROUTINE_NAME = :proc"
                ),
                {"db": db_target, "proc": proc_name},
            ).fetchone()
            return row[0] if row and row[0] else "-- Procedure body not accessible"
        return "-- Not available for this dialect"


def list_triggers(engine: Engine, database: str = "") -> list[str]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
            ).fetchall()
            return [r[0] for r in rows]
        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            rows = conn.execute(
                text("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = :db"),
                {"db": db_target},
            ).fetchall()
            return [r[0] for r in rows]
        else:
            rows = conn.execute(
                text("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = 'public'")
            ).fetchall()
            return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Stats & DDL
# ---------------------------------------------------------------------------

def get_table_stats(engine: Engine, database: str, table: str) -> dict[str, Any]:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            try:
                tbl_clean = table.replace('"', '""')
                count_row = conn.execute(text(f'SELECT COUNT(*) FROM "{tbl_clean}"')).fetchone()
                row_count = count_row[0] if count_row else 0
            except Exception:
                row_count = 0
            return {
                "approx_rows": row_count,
                "data_size_bytes": row_count * 64,
                "index_size_bytes": 0,
            }

        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            row = conn.execute(
                text(
                    "SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl"
                ),
                {"db": db_target, "tbl": table},
            ).fetchone()
            if row:
                return {
                    "approx_rows": row[0] or 0,
                    "data_size_bytes": row[1] or 0,
                    "index_size_bytes": row[2] or 0,
                }
            return {"approx_rows": 0, "data_size_bytes": 0, "index_size_bytes": 0}

        else:
            row = conn.execute(
                text(
                    "SELECT n_live_tup, pg_table_size(relid), pg_indexes_size(relid) "
                    "FROM pg_stat_user_tables WHERE relname = :tbl"
                ),
                {"tbl": table},
            ).fetchone()
            if row:
                return {
                    "approx_rows": row[0] or 0,
                    "data_size_bytes": row[1] or 0,
                    "index_size_bytes": row[2] or 0,
                }
            return {"approx_rows": 0, "data_size_bytes": 0, "index_size_bytes": 0}


def get_ddl(engine: Engine, database: str, table: str) -> str:
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if dialect == "sqlite":
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type IN ('table', 'view') AND name = :tbl"),
                {"tbl": table},
            ).fetchone()
            return (row[0] + ";") if row and row[0] else "-- DDL not found"

        elif dialect == "mysql":
            db_target = _resolve_mysql_db(conn, engine, database)
            try:
                row = conn.execute(text(f"SHOW CREATE TABLE `{db_target}`.`{table}`")).fetchone()
                return (row[1] + ";") if row else "-- DDL not found"
            except Exception:
                row = conn.execute(text(f"SHOW CREATE TABLE `{table}`")).fetchone()
                return (row[1] + ";") if row else "-- DDL not found"

        else:
            return f"-- PostgreSQL DDL for table {table}\nSELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}';"


def get_grants(engine: Engine) -> list[str]:
    dialect = engine.dialect.name
    if dialect == "mysql":
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SHOW GRANTS")).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []
    return []
