import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import get_page, get_exact_count

st.set_page_config(page_title="Data Viewer — SQL Helper", page_icon="📋", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("📋 Paginated Data Viewer")
st.caption("Browse table records with custom filtering, pagination, sorting, and multi-format exports.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to view data.")
    st.stop()

from app.db.connections import persist_active_database

dialect = engine.dialect.name

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
            key="viewer_db_switch",
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

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    table = st.selectbox("Select Table", tables)
with col2:
    page_size = st.selectbox("Rows per page", [25, 50, 100, 250, 500], index=1)
with col3:
    st.write("")
    st.write("")
    count_btn = st.button("🔢 Refresh Count", use_container_width=True)

if not table:
    st.stop()

with st.expander("🔍 Filters & Ordering", expanded=False):
    f_col1, f_col2 = st.columns(2)
    where_clause = f_col1.text_input("WHERE Clause (e.g. `is_active = 1` or `price > 50`)", value="")
    order_by = f_col2.text_input("ORDER BY (e.g. `id DESC` or `created_at ASC`)", value="")

if "data_page" not in st.session_state:
    st.session_state.data_page = 0

# Count total rows
if count_btn or st.session_state.get("last_count_table") != table:
    try:
        total = get_exact_count(engine, selected_db, table, where_clause)
        st.session_state.row_count = total
        st.session_state.last_count_table = table
    except Exception as e:
        st.error(f"Count error: {e}")

total = st.session_state.get("row_count")
if total is not None:
    st.caption(f"Total matching rows: **{total:,}**")

try:
    df = get_page(engine, selected_db, table, st.session_state.data_page, page_size, where_clause, order_by)
except Exception as e:
    st.error(f"Query error: {e}")
    st.stop()

st.dataframe(df, use_container_width=True, hide_index=True)

# Pagination controls
p_col1, p_col2, p_col3 = st.columns([1, 2, 1])
with p_col1:
    if st.button("◀ Previous Page", disabled=st.session_state.data_page == 0, use_container_width=True):
        st.session_state.data_page -= 1
        st.rerun()
with p_col2:
    start_row = st.session_state.data_page * page_size + 1
    end_row = st.session_state.data_page * page_size + len(df)
    st.markdown(f"<div style='text-align:center; color:#94a3b8; padding-top:8px;'>Page <b>{st.session_state.data_page + 1}</b> · Showing rows <b>{start_row}–{end_row}</b></div>", unsafe_allow_html=True)
with p_col3:
    if st.button("Next Page ▶", disabled=len(df) < page_size, use_container_width=True):
        st.session_state.data_page += 1
        st.rerun()

# Export options
if not df.empty:
    st.divider()
    e_col1, e_col2, _ = st.columns([1, 1, 2])
    with e_col1:
        csv = df.to_csv(index=False).encode()
        st.download_button("⬇ Export Page CSV", csv, file_name=f"{table}_page{st.session_state.data_page + 1}.csv", mime="text/csv", use_container_width=True)
    with e_col2:
        json_str = df.to_json(orient="records", indent=2).encode()
        st.download_button("⬇ Export Page JSON", json_str, file_name=f"{table}_page{st.session_state.data_page + 1}.json", mime="application/json", use_container_width=True)
