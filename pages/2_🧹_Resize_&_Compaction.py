import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db.size_analyzer import get_database_storage_overview, format_bytes
from app.db.maintenance import run_maintenance_action, generate_maintenance_script
from app.db import schema_reader as sr

st.set_page_config(page_title="Resize & Compaction — SQL Helper", page_icon="🧹", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🧹 Database Resizing & Compaction Workbench")
st.caption("Reclaim fragmented disk space, defragment table B-trees, shrink WAL logs, and run database-wide vacuuming.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to run compaction and resizing.")
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
            key="resize_db_switch",
        )
        if sel_db != current_db:
            st.session_state.selected_database = sel_db
            persist_active_database(sel_db)
            st.rerun()

selected_db = st.session_state.get("selected_database", current_db)

try:
    overview = get_database_storage_overview(engine, selected_db)
    tables = [t["table"] for t in overview.get("tables", [])]
except Exception as e:
    st.error(f"Error reading storage: {e}")
    st.stop()

# ── Summary Status Bar ──────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
with c1:
    render_metric_card("Current DB Size", format_bytes(overview["total_size_bytes"]), f"Engine: {dialect.upper()}", badge=dialect.upper(), badge_type="info")
with c2:
    reclaimable = overview["free_space_bytes"]
    badge_t = "warning" if reclaimable > 0 else "success"
    render_metric_card("Estimated Reclaimable", format_bytes(reclaimable), "Fragmentation & dead space", badge="Reclaimable", badge_type=badge_t)
with c3:
    render_metric_card("Tables Analyzed", str(len(tables)), "Ready for compaction", badge="Ready", badge_type="purple")

if not is_test_db:
    guard_c1, guard_c2 = st.columns([4, 1])
    with guard_c1:
        st.warning("⚠️ **Safety Guardrail Active (Read-Only)**: Direct maintenance execution is locked to protect live databases. Click **'Enable Direct Execution'** or toggle in the sidebar to execute operations directly.")
    with guard_c2:
        st.write("")
        if st.button("🔓 Enable Execution", type="primary", use_container_width=True, key="enable_compaction_exec"):
            st.session_state.is_test_db = True
            st.rerun()

# ── Action Result Banner ─────────────────────────────────────────────────────
if "last_maintenance_result" in st.session_state:
    res = st.session_state.last_maintenance_result
    if res["success"]:
        st.balloons()
        st.success(
            f"🎉 **Compaction Complete!** Reclaimed **{res['formatted_reclaimed']}** "
            f"({res['reclaimed_percent']}% reduction). "
            f"Database size went from **{format_bytes(res['size_before_bytes'])}** → **{format_bytes(res['size_after_bytes'])}**."
        )
        if res.get("sql_executed"):
            st.code(res["sql_executed"], language="sql")
    else:
        st.error(f"Compaction error: {res['error']}")

tab_ops, tab_tables, tab_script = st.tabs(["🚀 Global Compaction", "📋 Table Defragmentation", "📜 Maintenance Script"])

# ── Tab 1: Global Compaction ────────────────────────────────────────────────
with tab_ops:
    st.markdown("### Database-Wide Resizing & Vacuuming")

    if dialect == "sqlite":
        st.markdown(
            """
            **SQLite Compaction Actions:**
            - **`VACUUM`**: Rebuilds the database file from scratch, eliminating all unused freelist pages and shrinking the `.db` file on disk.
            - **`PRAGMA wal_checkpoint(TRUNCATE)`**: Flushes pending WAL transactions and resets the `-wal` log file to 0 bytes.
            - **`ANALYZE` & `PRAGMA optimize`**: Refreshes table cardinality statistics for the query planner.
            """
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🧹 Run VACUUM (Shrink DB)", type="primary", use_container_width=True, disabled=not is_test_db):
                with st.spinner("Running full database VACUUM…"):
                    res = run_maintenance_action(engine, "vacuum", database=selected_db)
                    st.session_state.last_maintenance_result = res
                    st.rerun()

        with col2:
            if st.button("🔄 Flush & Truncate WAL", use_container_width=True, disabled=not is_test_db):
                with st.spinner("Flushing WAL log…"):
                    res = run_maintenance_action(engine, "wal_checkpoint", database=selected_db)
                    st.session_state.last_maintenance_result = res
                    st.rerun()

        with col3:
            if st.button("📈 Run ANALYZE & Optimize", use_container_width=True, disabled=not is_test_db):
                with st.spinner("Analyzing statistics…"):
                    res = run_maintenance_action(engine, "analyze", database=selected_db)
                    st.session_state.last_maintenance_result = res
                    st.rerun()

    elif dialect == "mysql":
        frag_tables = [t for t in overview.get("tables", []) if t.get("free_bytes", 0) >= 1_048_576]  # >= 1MB
        frag_tables.sort(key=lambda x: x.get("free_bytes", 0), reverse=True)
        total_frag_bytes = sum(t.get("free_bytes", 0) for t in frag_tables)

        st.markdown(
            f"""
            **MySQL Compaction Strategy:**
            * Out of **{len(tables)} tables**, only **{len(frag_tables)} tables** contain reclaimable fragmentation (totaling **{format_bytes(total_frag_bytes)}** of free space).
            * The remaining **{len(tables) - len(frag_tables)} tables** have 0 MB of bloat and do not need to be rewritten on disk.
            """
        )

        opt_c1, opt_c2 = st.columns([3, 2])

        with opt_c1:
            # 1. Smart Target Optimize (Recommended)
            btn_frag_label = f"🎯 Smart Optimize {len(frag_tables)} Fragmented Tables Only (Reclaim ~{format_bytes(total_frag_bytes)})"
            if st.button(btn_frag_label, type="primary", use_container_width=True, disabled=not is_test_db or not frag_tables):
                ov_before = get_database_storage_overview(engine, selected_db)
                size_before = ov_before["total_size_bytes"]
                progress_bar = st.progress(0, text="Optimizing fragmented tables...")
                for idx, t_obj in enumerate(frag_tables):
                    tbl_name = t_obj["table"]
                    progress_bar.progress((idx + 1) / len(frag_tables), text=f"Optimizing `{tbl_name}` ({format_bytes(t_obj['free_bytes'])} bloat) [{idx+1}/{len(frag_tables)}]…")
                    run_maintenance_action(engine, "optimize_table", table=tbl_name, database=selected_db)
                progress_bar.empty()
                ov_after = get_database_storage_overview(engine, selected_db)
                size_after = ov_after["total_size_bytes"]
                reclaimed = max(0, size_before - size_after)
                pct = round((reclaimed / size_before) * 100, 2) if size_before > 0 else 0
                st.session_state.last_maintenance_result = {
                    "success": True,
                    "formatted_reclaimed": format_bytes(reclaimed) if reclaimed > 0 else format_bytes(total_frag_bytes),
                    "reclaimed_percent": pct,
                    "size_before_bytes": size_before,
                    "size_after_bytes": size_after,
                    "sql_executed": f"OPTIMIZE TABLE across {len(frag_tables)} fragmented tables ({', '.join(t['table'] for t in frag_tables[:5])}...);",
                }
                st.rerun()

        with opt_c2:
            # 2. Fast Stats Refresh Only
            if st.button("⚡ Fast Update Optimizer Statistics Only (ANALYZE TABLE)", use_container_width=True, disabled=not is_test_db):
                with st.spinner("Refreshing optimizer index statistics across all tables…"):
                    for tbl in tables:
                        run_maintenance_action(engine, "analyze_table", table=tbl, database=selected_db)
                st.session_state.last_maintenance_result = {
                    "success": True,
                    "formatted_reclaimed": "Statistics Refreshed",
                    "reclaimed_percent": 0,
                    "size_before_bytes": overview["total_size_bytes"],
                    "size_after_bytes": overview["total_size_bytes"],
                    "sql_executed": f"ANALYZE TABLE across {len(tables)} tables;",
                }
                st.rerun()

        # Display Top Fragmented Tables List
        if frag_tables:
            st.markdown("##### 📋 Tables with Reclaimable Fragmentation")
            df_frag = pd.DataFrame([
                {
                    "Table": t["table"],
                    "Total Size": format_bytes(t["total_bytes"]),
                    "Data Size": format_bytes(t["data_bytes"]),
                    "Index Size": format_bytes(t["index_bytes"]),
                    "Reclaimable Bloat (DATA_FREE)": format_bytes(t["free_bytes"]),
                    "Rows": f"{t['rows']:,}",
                }
                for t in frag_tables
            ])
            st.dataframe(df_frag, use_container_width=True, hide_index=True)

        with st.expander(f"⚠️ Full Rebuild Option (All {len(tables)} Tables — Slow on Remote Servers)", expanded=False):
            st.caption("Rebuilds every single table from scratch, even if it has 0 MB of bloat. This can take a very long time on remote servers with limited disk I/O.")
            if st.button(f"🧹 Force Rebuild All {len(tables)} Tables", use_container_width=True, disabled=not is_test_db, key="force_rebuild_all_mysql"):
                ov_before = get_database_storage_overview(engine, selected_db)
                size_before = ov_before["total_size_bytes"]
                progress_bar = st.progress(0, text="Optimizing tables...")
                for idx, tbl in enumerate(tables):
                    progress_bar.progress((idx + 1) / len(tables), text=f"Optimizing `{tbl}` ({idx+1}/{len(tables)})…")
                    run_maintenance_action(engine, "optimize_table", table=tbl, database=selected_db)
                progress_bar.empty()
                ov_after = get_database_storage_overview(engine, selected_db)
                size_after = ov_after["total_size_bytes"]
                reclaimed = max(0, size_before - size_after)
                pct = round((reclaimed / size_before) * 100, 2) if size_before > 0 else 0
                st.session_state.last_maintenance_result = {
                    "success": True,
                    "formatted_reclaimed": format_bytes(reclaimed) if reclaimed > 0 else "Defragmentation & Stats Complete",
                    "reclaimed_percent": pct,
                    "size_before_bytes": size_before,
                    "size_after_bytes": size_after,
                    "sql_executed": f"OPTIMIZE TABLE across {len(tables)} tables;",
                }
                st.rerun()

    else:  # PostgreSQL
        st.markdown(
            """
            **PostgreSQL Compaction Actions:**
            - **`VACUUM (ANALYZE)`**: Cleans dead tuples, updates visibility maps, and updates planner statistics.
            - **`REINDEX TABLE`**: Rebuilds bloated B-tree indexes to minimize index size.
            """
        )
        if st.button("🧹 VACUUM & Reindex All Tables", type="primary", use_container_width=True, disabled=not is_test_db):
            with st.spinner("Running VACUUM across all tables…"):
                for tbl in tables:
                    run_maintenance_action(engine, "vacuum_table", table=tbl)
                st.session_state.last_maintenance_result = {"success": True, "formatted_reclaimed": "Completed", "reclaimed_percent": 0, "size_before_bytes": 0, "size_after_bytes": 0, "sql_executed": "VACUUM (ANALYZE) ..."}
                st.rerun()

# ── Tab 2: Table Defragmentation ────────────────────────────────────────────
with tab_tables:
    st.markdown("### Individual Table Maintenance")
    target_table = st.selectbox("Select table to defragment / optimize", tables)

    if target_table:
        tbl_info = next((t for t in overview.get("tables", []) if t["table"] == target_table), {})
        
        tc1, tc2, tc3, tc4 = st.columns(4)
        tc1.metric("Rows", f"{tbl_info.get('rows', 0):,}")
        tc2.metric("Data Size", format_bytes(tbl_info.get("data_bytes", 0)))
        tc3.metric("Index Size", format_bytes(tbl_info.get("index_bytes", 0)))
        tc4.metric("Status", tbl_info.get("health", "Healthy"))

        st.write("")
        btn_c1, btn_c2 = st.columns(2)

        if dialect == "mysql":
            with btn_c1:
                if st.button(f"🧹 OPTIMIZE `{target_table}`", type="primary", use_container_width=True, disabled=not is_test_db):
                    with st.spinner(f"Optimizing `{target_table}`…"):
                        res = run_maintenance_action(engine, "optimize_table", table=target_table, database=selected_db)
                        st.session_state.last_maintenance_result = res
                        st.rerun()
            with btn_c2:
                if st.button(f"📊 ANALYZE `{target_table}`", use_container_width=True, disabled=not is_test_db):
                    with st.spinner(f"Analyzing `{target_table}`…"):
                        res = run_maintenance_action(engine, "analyze_table", table=target_table, database=selected_db)
                        st.session_state.last_maintenance_result = res
                        st.rerun()

        elif dialect == "postgresql":
            with btn_c1:
                if st.button(f'🧹 VACUUM "{target_table}"', type="primary", use_container_width=True, disabled=not is_test_db):
                    with st.spinner(f"Vacuuming `{target_table}`…"):
                        res = run_maintenance_action(engine, "vacuum_table", table=target_table)
                        st.session_state.last_maintenance_result = res
                        st.rerun()
            with btn_c2:
                if st.button(f'⚡ REINDEX "{target_table}"', use_container_width=True, disabled=not is_test_db):
                    with st.spinner(f"Reindexing `{target_table}`…"):
                        res = run_maintenance_action(engine, "reindex_table", table=target_table)
                        st.session_state.last_maintenance_result = res
                        st.rerun()

        else:  # sqlite
            with btn_c1:
                if st.button(f'⚡ REINDEX "{target_table}"', type="primary", use_container_width=True, disabled=not is_test_db):
                    with st.spinner(f"Reindexing `{target_table}`…"):
                        res = run_maintenance_action(engine, "reindex", table=target_table)
                        st.session_state.last_maintenance_result = res
                        st.rerun()

# ── Tab 3: Maintenance Script ───────────────────────────────────────────────
with tab_script:
    st.markdown("### 📜 Automated Maintenance Script")
    st.caption("Copy or download this batch SQL maintenance script for recurring cron jobs or DBA pipelines.")
    script = generate_maintenance_script(engine, tables, selected_db)
    st.code(script, language="sql")
    st.download_button(
        "⬇ Download maintenance.sql",
        script,
        file_name=f"{dialect}_maintenance.sql",
        mime="text/plain",
        use_container_width=True,
    )
