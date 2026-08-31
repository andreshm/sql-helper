import streamlit as st
import pandas as pd
import altair as alt
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.size_analyzer import get_database_storage_overview, format_bytes
from app.db.connections import persist_active_database

st.set_page_config(page_title="Storage & Size — SQL Helper", page_icon="📊", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("📊 Database Storage & Size Analyzer")
st.caption("Deep storage footprint breakdown, table allocations, index overhead, and bloat analysis.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to view storage analysis.")
    st.stop()

dialect = engine.dialect.name
info = st.session_state.get("connection_info", {})

# ── Database Resolution & Switcher ──────────────────────────────────────────
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
            key="storage_db_switch",
        )
        if sel_db != current_db:
            st.session_state.selected_database = sel_db
            persist_active_database(sel_db)
            st.rerun()

selected_db = st.session_state.get("selected_database", current_db)

with st.spinner(f"Analyzing storage footprint for `{selected_db or 'database'}`…"):
    try:
        overview = get_database_storage_overview(engine, selected_db)
    except Exception as e:
        st.error(f"Error reading storage metrics: {e}")
        st.stop()

# ── KPI Header Bar ──────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    render_metric_card("Total Footprint", format_bytes(overview["total_size_bytes"]), f"Engine: {dialect.upper()}", badge=dialect.upper(), badge_type="info")
with c2:
    render_metric_card("Table Data", format_bytes(overview["data_size_bytes"]), f"{overview['table_count']} Tables", badge="Data", badge_type="success")
with c3:
    render_metric_card("Index Storage", format_bytes(overview["index_size_bytes"]), "Allocated to indexes", badge="Indexes", badge_type="purple")
with c4:
    free_b = overview["free_space_bytes"]
    b_type = "warning" if free_b > 0 else "success"
    render_metric_card("Reclaimable Bloat", format_bytes(free_b), "Fragmented space", badge="Reclaimable", badge_type=b_type)
with c5:
    render_metric_card("Total Rows", f"{overview['total_rows']:,}", "All records", badge="Rows", badge_type="info")

# ── Engine Details Expander ──────────────────────────────────────────────────
with st.expander("ℹ️ Storage Engine & Allocation Details", expanded=False):
    if dialect == "sqlite":
        ec1, ec2, ec3, ec4 = st.columns(4)
        ec1.metric("Page Size", f"{overview.get('page_size', 4096):,} bytes")
        ec2.metric("Total Pages", f"{overview.get('page_count', 0):,}")
        ec3.metric("Freelist Pages", f"{overview.get('freelist_count', 0):,}")
        ec4.metric("Auto-Vacuum", overview.get("auto_vacuum_mode", "NONE"))
        if overview.get("wal_size_bytes", 0) > 0:
            st.info(f"WAL File Active: `{format_bytes(overview['wal_size_bytes'])}`")
    elif dialect == "mysql":
        schema_used = overview.get("schema_name", selected_db)
        st.write(f"**Schema:** `{schema_used}` · **Buffer Pool Engine:** InnoDB")
    else:
        st.write(f"**PostgreSQL Catalog:** Database `{engine.url.database}` · **Tablespace:** default")

tables_data = overview.get("tables", [])
if not tables_data:
    st.warning(f"No tables found in database `{selected_db}`.")
    st.stop()

# ── Interactive Storage Visualizations ──────────────────────────────────────
st.markdown("### 📈 Storage Distribution & Allocation")

chart_col1, chart_col2 = st.columns([3, 2])

# Prepare dataframe for charts
df_tables = pd.DataFrame(tables_data)

with chart_col1:
    st.markdown("##### Top Tables by Total Size")
    df_sorted = df_tables.sort_values(by="total_bytes", ascending=False).head(10)
    
    # Melt for stacked bar chart: Data vs Index vs Free
    df_melted = df_sorted.melt(
        id_vars=["table"],
        value_vars=["data_bytes", "index_bytes", "free_bytes"],
        var_name="Category",
        value_name="Bytes",
    )
    category_map = {
        "data_bytes": "Table Data",
        "index_bytes": "Indexes",
        "free_bytes": "Free / Bloat",
    }
    df_melted["Category"] = df_melted["Category"].map(category_map)
    df_melted["Size_MB"] = df_melted["Bytes"] / (1024 * 1024)

    bar_chart = (
        alt.Chart(df_melted)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("table:N", title="Table", sort="-y"),
            y=alt.Y("Size_MB:Q", title="Size (MB)"),
            color=alt.Color(
                "Category:N",
                scale=alt.Scale(
                    domain=["Table Data", "Indexes", "Free / Bloat"],
                    range=["#38bdf8", "#a855f7", "#f59e0b"],
                ),
            ),
            tooltip=["table:N", "Category:N", alt.Tooltip("Size_MB:Q", format=".2f", title="Size (MB)")],
        )
        .properties(height=300)
    )
    st.altair_chart(bar_chart, use_container_width=True)

with chart_col2:
    st.markdown("##### Global Space Allocation")
    donut_data = pd.DataFrame([
        {"Category": "Table Data", "Bytes": overview["data_size_bytes"]},
        {"Category": "Indexes", "Bytes": overview["index_size_bytes"]},
        {"Category": "Free / Bloat", "Bytes": overview["free_space_bytes"]},
    ])
    donut_data = donut_data[donut_data["Bytes"] > 0]
    donut_data["Size_MB"] = donut_data["Bytes"] / (1024 * 1024)

    donut_chart = (
        alt.Chart(donut_data)
        .mark_arc(innerRadius=60, stroke="#1e293b", strokeWidth=2)
        .encode(
            theta=alt.Theta("Bytes:Q"),
            color=alt.Color(
                "Category:N",
                scale=alt.Scale(
                    domain=["Table Data", "Indexes", "Free / Bloat"],
                    range=["#38bdf8", "#a855f7", "#f59e0b"],
                ),
            ),
            tooltip=["Category:N", alt.Tooltip("Size_MB:Q", format=".2f", title="Size (MB)")],
        )
        .properties(height=300)
    )
    st.altair_chart(donut_chart, use_container_width=True)

# ── Detailed Table Storage Grid ─────────────────────────────────────────────
st.markdown("### 📋 Table Storage Breakdown")

display_rows = []
for t in tables_data:
    display_rows.append({
        "Table": t["table"],
        "Rows": f"{t['rows']:,}",
        "Data Size": format_bytes(t["data_bytes"]),
        "Index Size": format_bytes(t["index_bytes"]),
        "Total Size": format_bytes(t["total_bytes"]),
        "Reclaimable": format_bytes(t["free_bytes"]),
        "Indexes": t["index_count"],
        "Idx/Data Ratio": f"{t['index_to_data_ratio']:.2f}x",
        "Health Status": t["health"],
    })

df_display = pd.DataFrame(display_rows)
st.dataframe(df_display, use_container_width=True, hide_index=True)

st.divider()
st.markdown("##### 🚀 Quick Optimization Actions")
q_col1, q_col2, q_col3 = st.columns(3)
with q_col1:
    st.page_link("pages/2_🧹_Resize_&_Compaction.py", label="🧹 Reclaim Free Space & Shrink DB →", use_container_width=True)
with q_col2:
    st.page_link("pages/4_🔧_Data_Type_Optimizer.py", label="🔧 Downcast & Optimize Column Types →", use_container_width=True)
with q_col3:
    st.page_link("pages/3_⚡_Index_Advisor.py", label="⚡ Prune Unused / Redundant Indexes →", use_container_width=True)
