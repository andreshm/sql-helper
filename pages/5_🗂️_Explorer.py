import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import get_page
from app.db.size_analyzer import format_bytes
from app.db.erd_generator import generate_mermaid_erd, generate_graphviz_erd, get_schema_topology

st.set_page_config(page_title="Explorer — SQL Helper", page_icon="🗂️", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🗂️ Database Schema Explorer & ERD Graph")
st.caption("Inspect tables, views, columns, keys, indexes, triggers, and visualize interactive Entity-Relationship Diagrams (ERD).")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to explore schema.")
    st.stop()

selected_db = st.session_state.get("selected_database", "")
dialect = engine.dialect.name

# ── Database selection (for MySQL / Postgres) ────────────────────────────────
try:
    databases = sr.list_databases(engine)
except Exception as e:
    databases = ["main"] if dialect == "sqlite" else []

if dialect != "sqlite":
    current_db = st.session_state.get("selected_database")
    if not current_db or current_db not in databases:
        current_db = databases[0] if databases else ""

    selected_db = st.selectbox("Database / Schema", databases, index=databases.index(current_db) if current_db in databases else 0)
    st.session_state.selected_database = selected_db
else:
    selected_db = "main"
    st.session_state.selected_database = "main"

# ── Object Loading ──────────────────────────────────────────────────────────
try:
    tables = sr.list_tables(engine, selected_db)
    views = sr.list_views(engine, selected_db)
    procedures = sr.list_procedures(engine, selected_db)
    triggers = sr.list_triggers(engine, selected_db)
except Exception as e:
    st.error(f"Error loading objects: {e}")
    st.stop()

# ── Top-Level Navigation Tabs: Object Inspector vs Interactive ERD ──────────
tab_inspector, tab_erd = st.tabs(["📋 Object Inspector", "🗺️ Interactive Schema ERD & Relationships"])

# ════════════════════════════════════════════════════════════════════════════
# Tab 1: Object Inspector
# ════════════════════════════════════════════════════════════════════════════
with tab_inspector:
    col_tree, col_main = st.columns([1, 3])

    with col_tree:
        st.markdown("##### 📁 Schema Objects")
        search_q = st.text_input("Filter objects…", placeholder="Search table or view name", key="exp_search_q")
        
        filtered_tables = [t for t in tables if search_q.lower() in t.lower()] if search_q else tables
        filtered_views = [v for v in views if search_q.lower() in v.lower()] if search_q else views

        with st.expander(f"📋 Tables ({len(filtered_tables)})", expanded=True):
            for tbl in filtered_tables:
                is_sel = st.session_state.get("selected_table") == tbl and st.session_state.get("selected_object_type") == "table"
                btn_type = "primary" if is_sel else "secondary"
                if st.button(f"📋 {tbl}", key=f"tbl_btn_{tbl}", use_container_width=True, type=btn_type):
                    st.session_state.selected_table = tbl
                    st.session_state.selected_object_type = "table"
                    st.rerun()

        if views:
            with st.expander(f"👁️ Views ({len(filtered_views)})"):
                for v in filtered_views:
                    if st.button(f"👁️ {v}", key=f"view_btn_{v}", use_container_width=True):
                        st.session_state.selected_table = v
                        st.session_state.selected_object_type = "view"
                        st.rerun()

        if procedures:
            with st.expander(f"⚙️ Procedures ({len(procedures)})"):
                for p in procedures:
                    if st.button(f"⚙️ {p}", key=f"proc_btn_{p}", use_container_width=True):
                        st.session_state.selected_table = p
                        st.session_state.selected_object_type = "procedure"
                        st.rerun()

        if triggers:
            with st.expander(f"⚡ Triggers ({len(triggers)})"):
                for trg in triggers:
                    st.write(f"⚡ `{trg}`")

    with col_main:
        selected = st.session_state.get("selected_table")
        obj_type = st.session_state.get("selected_object_type", "table")

        if not selected:
            if tables:
                selected = tables[0]
                st.session_state.selected_table = selected
                st.session_state.selected_object_type = "table"
            else:
                st.info("No tables found in this schema.")
                st.stop()

        st.markdown(f"### {'📋' if obj_type == 'table' else '👁️'} `{selected}`")

        if obj_type == "procedure":
            st.subheader("Procedure Body")
            body = sr.get_procedure_body(engine, selected_db, selected)
            st.code(body, language="sql")
            st.stop()

        tab_struct, tab_indexes, tab_fk, tab_sample, tab_ddl, tab_stats = st.tabs(
            ["Columns", "Indexes", "Foreign Keys", "Sample Data", "DDL", "Stats"]
        )

        with tab_struct:
            try:
                cols = sr.get_columns(engine, selected_db, selected)
                df_cols = pd.DataFrame(cols)
                st.dataframe(df_cols, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Could not load columns: {e}")

        with tab_indexes:
            try:
                indexes = sr.get_indexes(engine, selected_db, selected)
                if indexes:
                    rows = []
                    for idx in indexes:
                        rows.append({
                            "Index Name": idx["name"],
                            "Unique": "✓" if idx.get("unique") else "",
                            "Type": idx.get("type", "BTREE"),
                            "Columns": ", ".join(idx.get("columns", [])),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                else:
                    st.info("No indexes found on this table.")
            except Exception as e:
                st.error(f"Could not load indexes: {e}")

        with tab_fk:
            try:
                fks = sr.get_foreign_keys(engine, selected_db, selected)
                if fks:
                    st.dataframe(pd.DataFrame(fks), use_container_width=True, hide_index=True)
                else:
                    st.info("No foreign keys found on this table.")
            except Exception as e:
                st.error(f"Could not load foreign keys: {e}")

        with tab_sample:
            try:
                sample_df = get_page(engine, selected_db, selected, page=0, page_size=10)
                if not sample_df.empty:
                    st.dataframe(sample_df, use_container_width=True, hide_index=True)
                else:
                    st.info("Table is currently empty.")
            except Exception as e:
                st.error(f"Could not load sample data: {e}")

        with tab_ddl:
            try:
                ddl = sr.get_ddl(engine, selected_db, selected)
                st.code(ddl, language="sql")
                st.download_button("⬇ Download DDL", ddl, file_name=f"{selected}.sql", mime="text/plain", key="btn_download_tbl_ddl")
            except Exception as e:
                st.error(f"Could not load DDL: {e}")

        with tab_stats:
            try:
                stats = sr.get_table_stats(engine, selected_db, selected)
                s1, s2, s3 = st.columns(3)
                s1.metric("Approx. Rows", f"{stats.get('approx_rows', 0):,}")
                s2.metric("Data Footprint", format_bytes(stats.get("data_size_bytes", 0)))
                s3.metric("Index Footprint", format_bytes(stats.get("index_size_bytes", 0)))
            except Exception as e:
                st.error(f"Could not load stats: {e}")

# ════════════════════════════════════════════════════════════════════════════
# Tab 2: Interactive Schema ERD & Relationships
# ════════════════════════════════════════════════════════════════════════════
with tab_erd:
    st.markdown("### 🗺️ Visual Entity-Relationship Diagram (ERD)")
    st.caption("Interactive visual schema graph mapping primary keys, foreign key constraints, column datatypes, and table cardinality.")

    if not tables:
        st.info("No tables available to generate an ERD.")
    else:
        erd_c1, erd_c2, erd_c3 = st.columns([2, 1, 1])
        with erd_c1:
            selected_erd_tables = st.multiselect(
                "Filter Specific Tables",
                tables,
                default=tables[:15] if len(tables) > 15 else tables,
                help="Select tables to visualize. Leave empty to show all tables (capped at 50 for performance).",
                key="erd_multiselect_tables",
            )
        with erd_c2:
            include_ref = st.checkbox("🔗 Auto-Include Foreign Tables", value=True, help="Automatically include tables referenced by foreign keys")
            diagram_engine = st.radio("Renderer", ["Mermaid ERD", "Graphviz DOT"], horizontal=True, key="erd_renderer")
        with erd_c3:
            st.write("")
            all_selected = st.button("🌐 Show All Tables", key="btn_erd_show_all", use_container_width=True)
            if all_selected:
                selected_erd_tables = tables

        target_tables = selected_erd_tables if selected_erd_tables else tables

        with st.spinner("Generating Entity-Relationship Diagram…"):
            if diagram_engine == "Mermaid ERD":
                mermaid_code = generate_mermaid_erd(
                    engine,
                    selected_db,
                    selected_tables=target_tables,
                    include_referenced=include_ref,
                )
                
                # Render Mermaid diagram
                st.markdown(f"```mermaid\n{mermaid_code}\n```")
                
                with st.expander("📝 View Mermaid Markup & Code", expanded=False):
                    st.code(mermaid_code, language="text")
                    st.download_button("⬇ Download Mermaid (.mmd)", mermaid_code, file_name=f"{selected_db}_erd.mmd", mime="text/plain", key="btn_dl_mermaid")
            else:
                dot_code = generate_graphviz_erd(
                    engine,
                    selected_db,
                    selected_tables=target_tables,
                    include_referenced=include_ref,
                )
                try:
                    st.graphviz_chart(dot_code, use_container_width=True)
                except Exception as g_err:
                    st.warning(f"Graphviz preview: {g_err}. You can view the raw DOT code below.")

                with st.expander("📝 View Graphviz DOT Code", expanded=False):
                    st.code(dot_code, language="text")
                    st.download_button("⬇ Download Graphviz (.dot)", dot_code, file_name=f"{selected_db}_erd.dot", mime="text/plain", key="btn_dl_dot")
