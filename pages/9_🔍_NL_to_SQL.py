import re
import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import execute_query
from app.ai.nl_to_sql import build_nl_to_sql_prompt, build_explain_prompt
from app.ai.provider import ask

st.set_page_config(page_title="NL → SQL — SQL Helper", page_icon="🔍", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🔍 Natural Language → SQL Generator")
st.caption("Convert plain-English requests into dialect-optimized SQL queries with instant EXPLAIN execution plans.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to generate SQL queries.")
    st.stop()

from app.db.connections import persist_active_database

dialect = engine.dialect.name
is_test_db = st.session_state.get("is_test_db", False)
cfg = st.session_state.get("config", {})

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
            key="nl_db_switch",
        )
        if sel_db != current_db:
            st.session_state.selected_database = sel_db
            persist_active_database(sel_db)
            st.rerun()

selected_db = st.session_state.get("selected_database", current_db)

try:
    all_tables = sr.list_tables(engine, selected_db)
except Exception as e:
    st.error(f"Could not load tables: {e}")
    st.stop()

if "nl_history" not in st.session_state:
    st.session_state.nl_history = []

question = st.text_area(
    "Describe what you want to query in plain English:",
    height=90,
    placeholder="e.g. Show top 5 customers with the most completed orders and their total spending amount.",
    key="nl_input_box",
)

with st.expander("⚙️ Query Optimization Preferences", expanded=False):
    col_t, col_h = st.columns([1, 1])
    with col_t:
        focus_tables = st.multiselect("Focus Tables (leave empty for auto-detect)", all_tables)
    with col_h:
        h_perf = st.checkbox("Optimize for performance & index usage", value=True)
        h_joins = st.checkbox("Prefer explicit JOINs over subqueries", value=True)
        h_cte = st.checkbox("Use Common Table Expressions (CTEs)")
        h_limit = st.checkbox("Add safety LIMIT clause", value=True)

hints = []
if h_perf: hints.append("Optimize for query performance — leverage existing indexes")
if h_joins: hints.append("Prefer explicit JOINs over correlated subqueries")
if h_cte: hints.append("Use CTEs (WITH clauses) for readable multi-step transformations")
if h_limit: hints.append("Add LIMIT 1000 unless aggregating")

if st.button("✨ Generate Optimized SQL", type="primary", disabled=not question.strip()):
    # Build schema summary
    schema_lines = [f"Database Engine: {dialect.upper()} (Database: {selected_db})"]
    for tbl in all_tables[:40]:
        try:
            cols = sr.get_columns(engine, selected_db, tbl)
            col_parts = [f"{c['column']} ({c['type']}{' PK' if c.get('key')=='PRI' else ''})" for c in cols]
            schema_lines.append(f"  Table `{tbl}`: {', '.join(col_parts)}")
        except Exception:
            schema_lines.append(f"  Table `{tbl}`")
    schema_ctx = "\n".join(schema_lines)

    prompt = build_nl_to_sql_prompt(dialect, selected_db, question.strip(), schema_ctx, focus_tables, hints)

    with st.spinner("Generating query with AI…"):
        response = ask(cfg, prompt)

    st.session_state.nl_last_response = response
    st.session_state.nl_last_question = question.strip()
    st.session_state.nl_history.insert(0, {"question": question.strip(), "response": response})

# ── Render Result ────────────────────────────────────────────────────────────
response = st.session_state.get("nl_last_response")
if response:
    st.divider()

    def _extract_sql(text: str) -> str:
        m = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        for line in text.splitlines():
            if re.match(r"\s*(SELECT|WITH|INSERT|UPDATE|DELETE)\b", line, re.IGNORECASE):
                return line.strip()
        return ""

    sql = _extract_sql(response)

    if sql:
        st.markdown("### 💻 Generated SQL")
        st.code(sql, language="sql")

        b_col1, b_col2, b_col3, b_col4 = st.columns(4)
        if b_col1.button("📋 Send to Query Editor", use_container_width=True):
            st.session_state.editor_sql = sql
            st.success("Sent to Query Builder page!")

        if b_col2.button("▶ Execute Query", type="primary", use_container_width=True):
            with st.spinner("Executing query…"):
                df, err, affected = execute_query(engine, sql, database=selected_db)
            if err:
                st.error(f"Execution Error: {err}")
            elif df is not None:
                st.success(f"Returned **{len(df):,} rows**.")
                st.dataframe(df, use_container_width=True, hide_index=True)

        if b_col3.button("🔬 Run EXPLAIN Plan", use_container_width=True):
            explain_sql = f"EXPLAIN {sql}" if dialect in ("sqlite", "mysql") else f"EXPLAIN (FORMAT TEXT) {sql}"
            if dialect == "sqlite":
                explain_sql = f"EXPLAIN QUERY PLAN {sql}"

            with st.spinner("Running EXPLAIN…"):
                df_exp, err_exp, _ = execute_query(engine, explain_sql, database=selected_db)
            if err_exp:
                st.error(f"EXPLAIN error: {err_exp}")
            else:
                st.markdown("#### 🔬 Execution Plan Output")
                st.dataframe(df_exp, use_container_width=True, hide_index=True)

        b_col4.download_button("⬇ Download .sql", sql, file_name="generated_query.sql", mime="text/plain", use_container_width=True)

    with st.expander("💡 AI Explanation & Alternatives", expanded=True):
        st.markdown(response)
