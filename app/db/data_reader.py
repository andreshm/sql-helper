from __future__ import annotations
import pandas as pd
from sqlalchemy import text, Engine


def get_page(
    engine: Engine,
    database: str,
    table: str,
    page: int = 0,
    page_size: int = 100,
    where_clause: str = "",
    order_by: str = "",
) -> pd.DataFrame:
    dialect = engine.dialect.name
    if dialect == "mysql":
        full_table = f"`{database}`.`{table}`" if database else f"`{table}`"
    else:
        full_table = f'"{table}"'

    sql = f"SELECT * FROM {full_table}"
    if where_clause.strip():
        sql += f" WHERE {where_clause}"
    if order_by.strip():
        sql += f" ORDER BY {order_by}"
    sql += f" LIMIT {page_size} OFFSET {page * page_size}"

    with engine.connect() as conn:
        if dialect == "mysql" and database:
            try:
                conn.execute(text(f"USE `{database}`;"))
            except Exception:
                pass
        result = conn.execute(text(sql))
        rows = result.fetchall()
        cols = list(result.keys())
    return pd.DataFrame(rows, columns=cols)


def get_exact_count(
    engine: Engine,
    database: str,
    table: str,
    where_clause: str = "",
) -> int:
    dialect = engine.dialect.name
    if dialect == "mysql":
        full_table = f"`{database}`.`{table}`" if database else f"`{table}`"
    else:
        full_table = f'"{table}"'

    sql = f"SELECT COUNT(*) FROM {full_table}"
    if where_clause.strip():
        sql += f" WHERE {where_clause}"

    with engine.connect() as conn:
        if dialect == "mysql" and database:
            try:
                conn.execute(text(f"USE `{database}`;"))
            except Exception:
                pass
        row = conn.execute(text(sql)).fetchone()
    return int(row[0]) if row else 0


def execute_query(
    engine: Engine,
    sql: str,
    database: str = "",
) -> tuple[pd.DataFrame | None, str, int]:
    """
    Execute arbitrary SQL with active database context.
    Returns (dataframe_or_None, error_message, affected_rows).
    """
    dialect = engine.dialect.name
    # Fallback to session state selected_database if database parameter was omitted
    if not database:
        try:
            import streamlit as st
            database = st.session_state.get("selected_database", "")
        except Exception:
            pass

    try:
        with engine.begin() as conn:
            # For MySQL, ensure the active database is selected if specified
            if dialect == "mysql" and database and database != "main":
                try:
                    conn.execute(text(f"USE `{database}`;"))
                except Exception:
                    pass

            statements = [s.strip() for s in sql.strip().split(";") if s.strip()]
            last_result = None
            for stmt in statements:
                last_result = conn.execute(text(stmt))

            if last_result and last_result.returns_rows:
                rows = last_result.fetchall()
                cols = list(last_result.keys())
                return pd.DataFrame(rows, columns=cols), "", 0
            else:
                return None, "", last_result.rowcount if last_result else 0
    except Exception as exc:
        return None, str(exc), 0
