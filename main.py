import os
import time
import streamlit as st
import pandas as pd
from pathlib import Path
from app.config import load_config
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import (
    render_connection_sidebar,
    _do_connect,
    auto_restore_session_if_needed,
)
from app.db.demo_db import generate_demo_database
from app.db import schema_reader as sr
from app.db.size_analyzer import get_database_storage_overview, format_bytes
from app.db.connections import save_last_session, get_last_session, get_password

st.set_page_config(
    page_title="SQL Helper — Database Workspace",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()

if "config" not in st.session_state:
    st.session_state.config = load_config()

if "engine" not in st.session_state:
    st.session_state.engine = None

if "connection_info" not in st.session_state:
    st.session_state.connection_info = {}

if "active_conn_id" not in st.session_state:
    st.session_state.active_conn_id = None

if "sidebar_mode" not in st.session_state:
    st.session_state.sidebar_mode = "list"

if "is_test_db" not in st.session_state:
    st.session_state.is_test_db = False

if "selected_tables" not in st.session_state:
    st.session_state.selected_tables = []

render_connection_sidebar()

# ── Auto-Resume Notification ────────────────────────────────────────────────
if st.session_state.get("auto_restored"):
    conn_name = st.session_state.get("connection_info", {}).get("name", "Database")
    active_db = st.session_state.get("selected_database", "main")
    st.toast(f"🔁 Resumed session: Connected to **{conn_name}** → Database `{active_db}`", icon="✅")
    st.session_state.auto_restored = False

# ── Header Banner ───────────────────────────────────────────────────────────
st.markdown(
    """
    <div class="sql-hero-card" style="padding:18px 24px; margin-bottom:16px;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
            <div>
                <h2 style="margin:0; font-size:1.8rem; font-weight:800; letter-spacing:-0.02em;">
                    🗄️ SQL Helper
                </h2>
                <p style="margin:4px 0 0 0; color:#94a3b8; font-size:0.95rem;">
                    Database Analyzer, Storage Resizer, Data Type Optimizer & Index Advisor
                </p>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

engine = st.session_state.get("engine")

# ════════════════════════════════════════════════════════════════════════════
# 1. NOT CONNECTED STATE -> EASY QUICK CONNECT WIZARD
# ════════════════════════════════════════════════════════════════════════════
if engine is None:
    st.markdown("### 🔌 Easy Connect & Quick Start")
    st.caption("Choose how you would like to connect to get started immediately:")

    w1, w2, w3 = st.columns(3)

    with w1:
        st.markdown(
            """
            <div class="sql-card" style="height:220px; display:flex; flex-direction:column; justify-content:space-between;">
                <div>
                    <h4 style="margin-top:0;">🚀 Instant Demo Database</h4>
                    <p style="color:#94a3b8; font-size:0.88rem;">
                        Load a pre-configured sample e-commerce database with intentional bloat, missing indexes, and oversized data types.
                    </p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("▶ Launch Demo Database", type="primary", use_container_width=True, key="main_demo_btn"):
            demo_path = generate_demo_database(force_recreate=False)
            conn_dict = {
                "id": "demo-ecommerce-db",
                "name": "Demo E-Commerce DB",
                "type": "sqlite",
                "sqlite_path": demo_path,
                "database": "main",
            }
            try:
                _do_connect(conn_dict, target_db="main")
                st.session_state.is_test_db = True
                st.rerun()
            except Exception as exc:
                st.error(f"Failed: {exc}")

    with w2:
        st.markdown(
            """
            <div class="sql-card" style="height:220px; display:flex; flex-direction:column; justify-content:space-between;">
                <div>
                    <h4 style="margin-top:0;">🪶 Local SQLite File</h4>
                    <p style="color:#94a3b8; font-size:0.88rem;">
                        Open any local <code>.db</code>, <code>.sqlite</code>, or <code>.sqlite3</code> file from your machine for instant offline analysis.
                    </p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.popover("📁 Open SQLite File", use_container_width=True):
            sqlite_file = st.text_input("Enter SQLite file path", value="demo_ecommerce.db")
            if st.button("Connect to SQLite", type="primary", use_container_width=True):
                if sqlite_file.strip():
                    conn_dict = {
                        "id": f"sqlite-{Path(sqlite_file).stem}",
                        "name": f"SQLite: {Path(sqlite_file).name}",
                        "type": "sqlite",
                        "sqlite_path": sqlite_file.strip(),
                        "database": "main",
                    }
                    try:
                        _do_connect(conn_dict, target_db="main")
                        st.session_state.is_test_db = True
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed: {exc}")

    with w3:
        st.markdown(
            """
            <div class="sql-card" style="height:220px; display:flex; flex-direction:column; justify-content:space-between;">
                <div>
                    <h4 style="margin-top:0;">🐘 / 🐬 MySQL / PostgreSQL</h4>
                    <p style="color:#94a3b8; font-size:0.88rem;">
                        Connect to a remote or local database server. Once connected, you can browse all databases.
                    </p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.popover("🔌 Connect to Server", use_container_width=True):
            s_type = st.selectbox("Engine", ["postgresql", "mysql", "mariadb"])
            col_h, col_p = st.columns([3, 1])
            s_host = col_h.text_input("Host", value="localhost")
            s_port = col_p.number_input("Port", value=5432 if s_type == "postgresql" else 3306)
            s_user = st.text_input("User", value="postgres" if s_type == "postgresql" else "root")
            s_pass = st.text_input("Password", type="password")
            s_db = st.text_input("Database (optional)", value="")
            if st.button("Connect & Browse Databases", type="primary", use_container_width=True):
                conn_dict = {
                    "id": f"{s_type}-{s_host}",
                    "name": f"{s_type.upper()} ({s_host})",
                    "type": s_type,
                    "host": s_host,
                    "port": s_port,
                    "user": s_user,
                    "database": s_db,
                }
                try:
                    _do_connect(conn_dict, password=s_pass, target_db=s_db)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Connection Failed: {exc}")

# ════════════════════════════════════════════════════════════════════════════
# 2. CONNECTED STATE -> DATABASE & TABLE PICKER WORKSPACE
# ════════════════════════════════════════════════════════════════════════════
else:
    info = st.session_state.connection_info
    dialect = engine.dialect.name

    # ── Step 1: List all available databases on server ──────────────────────────
    try:
        available_dbs = sr.list_databases(engine)
    except Exception as e:
        available_dbs = [info.get("database", "main")] if info.get("database") else ["main"]

    current_active_db = st.session_state.get("selected_database")
    if current_active_db not in available_dbs:
        current_active_db = available_dbs[0] if available_dbs else "main"
        st.session_state.selected_database = current_active_db
        save_last_session(info.get("id", ""), current_active_db, st.session_state.get("selected_tables", []))

    st.markdown("### 🗄️ Choose a Database to Work With")
    st.caption(f"Server: **{info.get('name', 'Active')}** ({dialect.upper()}) · Available Databases: **{len(available_dbs)}**")

    # Render Database Cards Grid
    db_cols = st.columns(min(len(available_dbs), 4))
    for i, db_name in enumerate(available_dbs):
        col = db_cols[i % 4]
        is_active = (db_name == current_active_db)

        with col:
            badge_html = '<span class="badge-pill badge-success">🟢 ACTIVE</span>' if is_active else '<span class="badge-pill badge-info">AVAILABLE</span>'
            border_color = "rgba(34, 197, 94, 0.5)" if is_active else "rgba(255, 255, 255, 0.08)"
            bg_color = "rgba(34, 197, 94, 0.08)" if is_active else "rgba(30, 41, 59, 0.45)"

            st.markdown(
                f"""
                <div style="background:{bg_color}; border:1px solid {border_color}; border-radius:12px; padding:14px; margin-bottom:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:700; font-size:1.05rem; color:#f8fafc;">📁 {db_name}</span>
                        {badge_html}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if not is_active:
                if st.button(f"👉 Select `{db_name}`", key=f"select_db_{db_name}", use_container_width=True):
                    st.session_state.selected_database = db_name
                    st.session_state.selected_table = None
                    if info.get("id"):
                        pwd = get_password(info["id"]) if info.get("type") != "sqlite" else ""
                        try:
                            _do_connect(info, password=pwd, target_db=db_name)
                        except Exception:
                            save_last_session(info.get("id", ""), db_name, st.session_state.get("selected_tables", []))
                    else:
                        save_last_session(info.get("id", ""), db_name, st.session_state.get("selected_tables", []))
                    st.rerun()
            else:
                st.button("✓ Active Database", key=f"active_db_{db_name}", use_container_width=True, disabled=True)

    st.divider()

    # ── Step 2: Active Database Storage & Tables Overview ────────────────────────
    selected_db = st.session_state.get("selected_database", current_active_db)

    try:
        overview = get_database_storage_overview(engine, selected_db)
        all_tables = [t["table"] for t in overview.get("tables", [])]
    except Exception as exc:
        st.error(f"Could not load database storage: {exc}")
        overview = {"total_size_bytes": 0, "data_size_bytes": 0, "index_size_bytes": 0, "free_space_bytes": 0, "total_rows": 0, "tables": []}
        all_tables = []

    # KPI Bar for Selected Database
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        render_metric_card("Active DB Size", format_bytes(overview["total_size_bytes"]), f"Database: `{selected_db}`", badge=selected_db, badge_type="info")
    with c2:
        render_metric_card("Table Data", format_bytes(overview["data_size_bytes"]), f"{len(all_tables)} Tables", badge="Data", badge_type="success")
    with c3:
        render_metric_card("Index Storage", format_bytes(overview["index_size_bytes"]), "Allocated indexes", badge="Indexes", badge_type="purple")
    with c4:
        free_b = overview["free_space_bytes"]
        render_metric_card("Reclaimable Bloat", format_bytes(free_b), "Fragmented space", badge="Bloat", badge_type="warning" if free_b > 0 else "success")
    with c5:
        render_metric_card("Total Rows", f"{overview['total_rows']:,}", "Across all tables", badge="Records", badge_type="info")

    st.write("")

    # ── Step 3: Optional Table Selection & Focus ─────────────────────────────────
    st.markdown("### 📋 Tables in Database")
    st.caption("Optionally select specific tables to focus on, or launch any analysis module:")

    t_col1, t_col2 = st.columns([3, 1])
    with t_col1:
        current_focus = st.session_state.get("selected_tables", [])
        valid_focus = [t for t in current_focus if t in all_tables]
        selected_focus_tables = st.multiselect(
            "Focus on specific tables (leave empty to analyze entire database):",
            options=all_tables,
            default=valid_focus,
            placeholder="Choose tables or leave empty for full database",
        )
        if selected_focus_tables != current_focus:
            st.session_state.selected_tables = selected_focus_tables
            save_last_session(info.get("id", ""), selected_db, selected_focus_tables)

    with t_col2:
        st.write("")
        st.write("")
        if st.button("🧹 Clear Table Focus", use_container_width=True):
            st.session_state.selected_tables = []
            save_last_session(info.get("id", ""), selected_db, [])
            st.rerun()

    # Table Storage & Status Grid
    tables_data = overview.get("tables", [])
    if selected_focus_tables:
        tables_data = [t for t in tables_data if t["table"] in selected_focus_tables]

    if tables_data:
        display_rows = []
        for t in tables_data:
            display_rows.append({
                "Table": t["table"],
                "Rows": f"{t['rows']:,}",
                "Data Footprint": format_bytes(t["data_bytes"]),
                "Index Footprint": format_bytes(t["index_bytes"]),
                "Total Size": format_bytes(t["total_bytes"]),
                "Reclaimable": format_bytes(t["free_bytes"]),
                "Index Count": t["index_count"],
                "Idx/Data Ratio": f"{t['index_to_data_ratio']:.2f}x",
                "Health Status": t["health"],
            })
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    # ── Step 4: Quick Launch Workbench Actions ──────────────────────────────────
    st.markdown("### 🚀 Launch Analysis & Optimization Workbenches")

    act_col1, act_col2, act_col3, act_col4 = st.columns(4)

    with act_col1:
        st.markdown(
            """
            <div class="sql-card" style="height:140px;">
                <h5 style="margin:0 0 6px 0;">📊 Storage & Size</h5>
                <p style="color:#94a3b8; font-size:0.82rem; margin:0;">
                    Visual storage breakdown, charts, and bloat rankings.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/1_📊_Storage_&_Size.py", label="Open Storage Visualizer →", use_container_width=True)

    with act_col2:
        st.markdown(
            """
            <div class="sql-card" style="height:140px;">
                <h5 style="margin:0 0 6px 0;">⚡ Index Advisor</h5>
                <p style="color:#94a3b8; font-size:0.82rem; margin:0;">
                    Find duplicate, redundant, and missing FK indexes.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/3_⚡_Index_Advisor.py", label="Open Index Advisor →", use_container_width=True)

    with act_col3:
        st.markdown(
            """
            <div class="sql-card" style="height:140px;">
                <h5 style="margin:0 0 6px 0;">🔧 Type Optimizer</h5>
                <p style="color:#94a3b8; font-size:0.82rem; margin:0;">
                    Downcast integers, shrink text, calculate MB savings.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/4_🔧_Data_Type_Optimizer.py", label="Open Type Optimizer →", use_container_width=True)

    with act_col4:
        st.markdown(
            """
            <div class="sql-card" style="height:140px;">
                <h5 style="margin:0 0 6px 0;">🧹 Resize & Compact</h5>
                <p style="color:#94a3b8; font-size:0.82rem; margin:0;">
                    Run VACUUM, defragment tables, shrink disk size.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link("pages/2_🧹_Resize_&_Compaction.py", label="Open Compaction →", use_container_width=True)

# ── Footer: Quit & Shut Down Server ──────────────────────────────────────────
st.divider()
f_col1, f_col2 = st.columns([4, 1])
with f_col1:
    st.caption("🗄️ SQL Helper · Universal Database Performance & Storage Suite")
with f_col2:
    with st.popover("🛑 Shut Down Server", use_container_width=True):
        st.warning("Stop SQL Helper and terminate the local Python server?")
        if st.button("Confirm & Exit", type="primary", use_container_width=True, key="main_quit_btn"):
            if st.session_state.get("engine"):
                try:
                    st.session_state.engine.dispose()
                except Exception:
                    pass
            st.success("Server stopped. You can close this tab.")
            time.sleep(0.5)
            os._exit(0)
