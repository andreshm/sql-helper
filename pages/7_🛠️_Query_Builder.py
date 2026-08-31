import streamlit as st
import pandas as pd
import time
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import execute_query
from app.ai.query_generator import (
    generate_insert_template,
    generate_update_template,
    generate_delete_template,
    build_query_review_prompt,
)
from app.ai.provider import ask

st.set_page_config(page_title="Query Builder — SQL Helper", page_icon="🛠️", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🛠️ SQL Query Workbench & Template Builder")
st.caption("Interactive SQL editor, automated DML statement generators, EXPLAIN execution plans, and AI query reviews.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to run queries.")
    st.stop()

from app.db.connections import persist_active_database

dialect = engine.dialect.name
is_test_db = st.session_state.get("is_test_db", False)

try:
    databases = sr.list_databases(engine)
except Exception:
    databases = ["main"] if dialect == "sqlite" else []

current_db = st.session_state.get("selected_database")
if not current_db or (databases and current_db not in databases):
    current_db = databases[0] if databases else ("main" if dialect == "sqlite" else "")
    st.session_state.selected_database = current_db
    persist_active_database(current_db)

if dialect != "sqlite" and len(databases) > 1:
    top_c1, top_c2 = st.columns([3, 1])
    with top_c1:
        st.markdown(f"Active Database: **`{current_db}`**")
    with top_c2:
        sel_db = st.selectbox(
            "Switch Database",
            databases,
            index=databases.index(current_db) if current_db in databases else 0,
            label_visibility="collapsed",
            key="qb_db_switch",
        )
        if sel_db != current_db:
            st.session_state.selected_database = sel_db
            persist_active_database(sel_db)
            st.rerun()

selected_db = st.session_state.get("selected_database", current_db)

try:
    tables = sr.list_tables(engine, selected_db)
except Exception as e:
    st.error(f"Could not list tables: {e}")
    st.stop()

if "query_history" not in st.session_state:
    st.session_state.query_history = []

tab_editor, tab_gen, tab_history = st.tabs(["💻 SQL Editor", "📝 Generate DML Template", "🕑 Query History"])

# ── Tab 1: SQL Editor ───────────────────────────────────────────────────────
with tab_editor:
    default_sql = st.session_state.get("editor_sql", f"SELECT * FROM {'customers' if 'customers' in tables else tables[0]} LIMIT 20;")

    try:
        from streamlit_ace import st_ace
        sql_input = st_ace(
            value=default_sql,
            language="sql",
            theme="monokai",
            height=220,
            key="ace_workbench",
            auto_update=True,
        )
    except ImportError:
        sql_input = st.text_area("SQL Statement", value=default_sql, height=180, key="sql_textarea")

    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    run_btn = col1.button("▶ Execute SQL", type="primary", use_container_width=True)
    explain_btn = col2.button("🔬 EXPLAIN Plan", use_container_width=True)
    review_btn = col3.button("🤖 AI Review", use_container_width=True)
    clear_btn = col4.button("Clear", use_container_width=True)

    if clear_btn:
        st.session_state.editor_sql = "SELECT 1;"
        st.rerun()

    if run_btn:
        if not sql_input.strip():
            st.warning("No SQL query provided.")
        else:
            is_destructive = any(
                kw in sql_input.upper()
                for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "VACUUM")
            )
            if is_destructive and not is_test_db:
                st.error("⚠️ Destructive execution blocked. Toggle **'Test DB Mode'** in the sidebar to execute writes and DDL.")
            else:
                t0 = time.perf_counter()
                with st.spinner("Executing query…"):
                    df, err, affected = execute_query(engine, sql_input, database=selected_db)
                elapsed_ms = (time.perf_counter() - t0) * 1000

                if err:
                    st.error(f"Execution Error: {err}")
                elif df is not None:
                    st.success(f"Returned **{len(df):,} rows** in **{elapsed_ms:.2f} ms**.")
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.session_state.query_history.append({"sql": sql_input, "result": f"{len(df)} rows", "time": f"{elapsed_ms:.1f}ms"})
                else:
                    st.success(f"Statement executed successfully in **{elapsed_ms:.2f} ms**. Affected rows: **{affected}**.")
                    st.session_state.query_history.append({"sql": sql_input, "result": f"{affected} rows affected", "time": f"{elapsed_ms:.1f}ms"})

    if explain_btn and sql_input.strip():
        explain_sql = f"EXPLAIN {sql_input}" if dialect in ("sqlite", "mysql") else f"EXPLAIN (FORMAT TEXT) {sql_input}"
        if dialect == "sqlite":
            explain_sql = f"EXPLAIN QUERY PLAN {sql_input}"

        with st.spinner("Generating execution plan…"):
            df_exp, err_exp, _ = execute_query(engine, explain_sql, database=selected_db)
        if err_exp:
            st.error(f"EXPLAIN Error: {err_exp}")
        else:
            st.markdown("#### 🔬 Execution Plan Output")
            st.dataframe(df_exp, use_container_width=True, hide_index=True)

    if review_btn and sql_input.strip():
        dialect_name = engine.dialect.name
        prompt = build_query_review_prompt(dialect_name, sql_input, f"Database: {selected_db}")
        with st.spinner("Reviewing query with AI…"):
            cfg = st.session_state.get("config", {})
            review = ask(cfg, prompt)
        st.markdown("#### 🤖 AI Query Analysis")
        st.markdown(review)

# ── Tab 2: Generate DML Template ────────────────────────────────────────────
with tab_gen:
    g_col1, g_col2 = st.columns([3, 1])
    with g_col1:
        tbl_template = st.selectbox("Target Table", tables, key="tpl_tbl")
    with g_col2:
        op = st.selectbox("DML Operation", ["INSERT", "UPDATE", "DELETE"], key="tpl_op")

    if tbl_template:
        cols = sr.get_columns(engine, selected_db, tbl_template)
        if op == "INSERT":
            sql_tpl = generate_insert_template(dialect, selected_db, tbl_template, cols)
        elif op == "UPDATE":
            sql_tpl = generate_update_template(dialect, selected_db, tbl_template, cols)
        else:
            sql_tpl = generate_delete_template(dialect, selected_db, tbl_template, cols)

        st.code(sql_tpl, language="sql")
        if st.button("📋 Send to SQL Editor", type="primary", use_container_width=True):
            st.session_state.editor_sql = sql_tpl
            st.success("Sent to SQL Editor tab!")

# ── Tab 3: History ──────────────────────────────────────────────────────────
with tab_history:
    hist = st.session_state.get("query_history", [])
    if not hist:
        st.info("No queries executed yet in this session.")
    else:
        for i, item in enumerate(reversed(hist)):
            with st.expander(f"Query {len(hist) - i} — {item.get('result', '')} ({item.get('time', '')})"):
                st.code(item["sql"], language="sql")
                if st.button("Load Query into Editor", key=f"hist_load_{i}"):
                    st.session_state.editor_sql = item["sql"]
                    st.rerun()
