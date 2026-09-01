import streamlit as st
import pandas as pd
from sqlalchemy import text
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import execute_query
from app.db.size_analyzer import format_bytes
from app.db.type_optimizer import (
    profile_table_columns,
    scan_all_tables_for_types,
    generate_database_type_migration_script,
)
from app.ai.type_advisor import (
    build_type_audit_prompt,
    parse_ai_type_audit,
    build_database_wide_type_audit_prompt,
    parse_database_wide_ai_type_audit,
)
from app.ai.provider import ask

st.set_page_config(page_title="Data Type Optimizer — SQL Helper", page_icon="🔧", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🔧 Data Type Optimizer & Storage Footprint Reducer")
st.caption("Conservative, single-pass column profiling with strict 3-tier safety risk classification, ID sequence protection, and AI domain audits.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to analyze column data types.")
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
            key="type_opt_db_switch",
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

if "executed_fixes" not in st.session_state:
    st.session_state.executed_fixes = set()

tab_all, tab_single, tab_script = st.tabs([
    "🌐 Database-Wide (All Tables Optimizer)",
    "📋 Single Table Deep Inspector",
    "📜 Full Database Migration Script"
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Database-Wide (All Tables Optimizer)
# ══════════════════════════════════════════════════════════════════════════════
with tab_all:
    st.markdown("### 🌐 Database-Wide All Tables Optimizer")
    st.caption("Scan all tables across the entire schema in a single pass to discover safe storage reductions and sanitize empty strings.")

    db_cache_key = f"db_wide_type_suggestions_{selected_db}"
    db_sql_cache_key = f"db_wide_type_script_{selected_db}"
    db_ai_cache_key = f"db_wide_ai_audit_{selected_db}"

    scan_c1, scan_c2 = st.columns([3, 1])
    with scan_c1:
        st.info(f"Database **`{selected_db}`** contains **{len(tables)} tables**. All suggestions are classified by growth risk (🟢 Safe, 🟡 Check Growth, 🔴 High Risk).")
    with scan_c2:
        st.write("")
        run_full_scan = st.button("🚀 Scan Entire Database", type="primary", use_container_width=True, key="btn_run_db_scan")

    if run_full_scan:
        progress_bar = st.progress(0, text="Initializing database scan...")
        results = scan_all_tables_for_types(engine, selected_db, sample_limit=1500, deep_verify=True)
        progress_bar.empty()
        st.session_state[db_cache_key] = results
        st.session_state[db_sql_cache_key] = generate_database_type_migration_script(results, dialect=dialect, database=selected_db)
        
        # Initialize checkboxes: ONLY SAFE items checked by default!
        for t_name, s_list in results.items():
            for idx, s in enumerate(s_list):
                st.session_state[f"db_chk_{t_name}_{idx}"] = s.get("default_checked", False)
        st.rerun()

    all_db_suggs = st.session_state.get(db_cache_key)

    if all_db_suggs is None:
        st.info(f"💡 Click **'🚀 Scan Entire Database'** above to analyze all {len(tables)} tables in `{selected_db}`.")
    elif not all_db_suggs:
        st.success("🎉 **Entire Database is Optimal!** All columns across all tables are perfectly typed with zero waste detected.")
    else:
        total_opt_tables = len(all_db_suggs)
        total_opt_cols = sum(len(s_list) for s_list in all_db_suggs.values())
        total_db_saved_bytes = sum(sum(s.get("est_total_saved_bytes", 0) for s in s_list) for s_list in all_db_suggs.values())

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tables with Optimizations", f"{total_opt_tables} / {len(tables)}")
        k2.metric("Columns to Optimize", str(total_opt_cols))
        k3.metric("Est. Total Storage Saved", format_bytes(total_db_saved_bytes))
        k4.metric("Engine", dialect.upper())

        st.divider()

        # Global Toolbar, Risk Filter & AI Auditor
        gt_col1, gt_col2, gt_col3, gt_col4 = st.columns([1, 1, 1, 2])
        if gt_col1.button("🟢 Select Safe Only", key="db_sel_safe", use_container_width=True):
            for t_name, s_list in all_db_suggs.items():
                for idx, s in enumerate(s_list):
                    st.session_state[f"db_chk_{t_name}_{idx}"] = (s.get("risk_level") == "SAFE")
            st.rerun()

        if gt_col2.button("☑️ Select All", key="db_sel_all", use_container_width=True):
            for t_name, s_list in all_db_suggs.items():
                for idx in range(len(s_list)):
                    st.session_state[f"db_chk_{t_name}_{idx}"] = True
            st.rerun()

        if gt_col3.button("⬜ Deselect All", key="db_desel_all", use_container_width=True):
            for t_name, s_list in all_db_suggs.items():
                for idx in range(len(s_list)):
                    st.session_state[f"db_chk_{t_name}_{idx}"] = False
            st.rerun()

        if gt_col4.button("🤖 Run Database-Wide AI Semantic Audit", key="btn_db_ai_audit", use_container_width=True):
            with st.spinner("Local AI is auditing domain safety across all candidate migrations…"):
                prompt = build_database_wide_type_audit_prompt(dialect, all_db_suggs)
                ai_raw = ask(cfg, prompt)
                parsed = parse_database_wide_ai_type_audit(ai_raw)
                st.session_state[db_ai_cache_key] = parsed
                st.session_state[f"{db_ai_cache_key}_raw"] = ai_raw
                # Automatically uncheck any CAUTION items globally
                for t_name, s_list in all_db_suggs.items():
                    t_key = t_name.lower()
                    t_audit = parsed.get(t_key, {})
                    for idx, s in enumerate(s_list):
                        c_key = s["column"].lower()
                        c_audit = t_audit.get(c_key, {})
                        if c_audit.get("status") == "CAUTION":
                            st.session_state[f"db_chk_{t_name}_{idx}"] = False
                st.session_state[db_sql_cache_key] = generate_database_type_migration_script(all_db_suggs, dialect=dialect, database=selected_db, ai_audit_map=parsed)
            st.rerun()

        if f"{db_ai_cache_key}_raw" in st.session_state:
            with st.expander("📖 Database-Wide AI Semantic Audit Report", expanded=False):
                st.markdown(st.session_state[f"{db_ai_cache_key}_raw"])

        db_ai_audit_map = st.session_state.get(db_ai_cache_key, {})
        selected_global_items = []

        # Display grouped by table
        for t_name, s_list in all_db_suggs.items():
            t_saved = sum(s.get("est_total_saved_bytes", 0) for s in s_list)
            t_audit = db_ai_audit_map.get(t_name.lower(), {})
            with st.expander(f"📁 Table **`{t_name}`** — {len(s_list)} Optimizations (Saves ~{format_bytes(t_saved)})", expanded=True):
                for idx, s in enumerate(s_list):
                    sql_stmt = s["sql"]
                    is_applied = sql_stmt in st.session_state.executed_fixes
                    col_key = s["column"].lower()
                    col_audit = t_audit.get(col_key, {})
                    ai_status = col_audit.get("status", "")
                    ai_analysis = col_audit.get("analysis", "")

                    with st.container():
                        r1, r2 = st.columns([1, 15])
                        with r1:
                            if is_applied:
                                st.markdown("✅")
                            else:
                                chk_key = f"db_chk_{t_name}_{idx}"
                                if chk_key not in st.session_state:
                                    st.session_state[chk_key] = s.get("default_checked", False) if ai_status != "CAUTION" else False
                                is_checked = st.checkbox("", key=chk_key)
                                if is_checked:
                                    selected_global_items.append((sql_stmt, s, t_name))

                        with r2:
                            b_html = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-info">{s.get("verification", "Verified")}</span>'
                            
                            # Add Risk Badge
                            r_level = s.get("risk_level", "SAFE")
                            if r_level == "SAFE":
                                b_html += ' <span class="badge-pill badge-success">🟢 Safe & Confirmed</span>'
                            elif r_level == "MAYBE":
                                b_html += ' <span class="badge-pill badge-warning">🟡 Check Growth</span>'
                            else:
                                b_html += ' <span class="badge-pill badge-danger">🔴 High Risk / Sequence</span>'

                            # Add AI Badge
                            if ai_status == "APPROVED":
                                b_html += ' <span class="badge-pill badge-success">🛡️ AI Approved</span>'
                            elif ai_status == "CAUTION":
                                b_html += ' <span class="badge-pill badge-warning">⚠️ AI Caution</span>'
                            elif ai_status == "ALTERNATIVE":
                                b_html += ' <span class="badge-pill badge-info">💡 AI Refinement</span>'

                            st.markdown(f"**🔧 `{s['column']}`**: `{s['current_type']}` → **`{s['suggested_type']}`** (Saves {s['formatted_savings']}) {b_html}", unsafe_allow_html=True)
                            st.caption(f"**Reason**: {s['reason']}")
                            
                            code_c1, code_c2 = st.columns([12, 3])
                            with code_c1:
                                st.code(sql_stmt, language="sql")
                            with code_c2:
                                st.write("")
                                if is_applied:
                                    st.button("✅ Applied", key=f"btn_run_single_db_{t_name}_{idx}", disabled=True, use_container_width=True)
                                else:
                                    if r_level != "SAFE":
                                        with st.popover("⚠️ Verify & Run", use_container_width=True):
                                            st.warning(f"**Growth Check Required for `{t_name}.{s['column']}`**:\nAltering column type may cause errors if future application inserts exceed `{s['suggested_type']}`.")
                                            conf_single = st.checkbox("I have verified future volume.", key=f"chk_conf_db_{t_name}_{idx}")
                                            if st.button("Confirm & Apply Fix", type="primary", disabled=not conf_single, key=f"btn_conf_run_db_{t_name}_{idx}"):
                                                if not is_test_db:
                                                    st.session_state.is_test_db = True
                                                with st.spinner(f"Applying fix on `{t_name}.{s['column']}`…"):
                                                    _, err, _ = execute_query(engine, sql_stmt, database=selected_db)
                                                    if err:
                                                        st.error(f"Error: {err}")
                                                    else:
                                                        st.session_state.executed_fixes.add(sql_stmt)
                                                        st.success(f"✅ Updated `{t_name}.{s['column']}`!")
                                                        st.rerun()
                                    else:
                                        run_lbl = "▶️ Run Fix" if is_test_db else "🔓 Run Fix"
                                        if st.button(run_lbl, key=f"btn_run_single_db_{t_name}_{idx}", use_container_width=True):
                                            if not is_test_db:
                                                st.session_state.is_test_db = True
                                            with st.spinner(f"Applying fix on `{t_name}.{s['column']}`…"):
                                                _, err, _ = execute_query(engine, sql_stmt, database=selected_db)
                                                if err:
                                                    st.error(f"Error: {err}")
                                                else:
                                                    st.session_state.executed_fixes.add(sql_stmt)
                                                    st.success(f"✅ Updated `{t_name}.{s['column']}`!")
                                                    st.rerun()

                            if ai_analysis:
                                st.info(f"🤖 **AI Domain Analysis**: {ai_analysis}")
                        st.write("")

        # Global Bottom Execution Button Toolbar with Safety Modal
        st.write("")
        st.divider()
        
        non_safe_selected = [item for item in selected_global_items if item[1].get("risk_level") != "SAFE"]
        selected_sqls_only = [item[0] for item in selected_global_items]

        b_c1, b_c2 = st.columns([2, 3])
        with b_c1:
            if non_safe_selected:
                with st.popover(f"⚠️ Confirm & Apply Fixes ({len(selected_global_sqls_only := selected_sqls_only)})", use_container_width=True):
                    st.warning(f"**Safety Confirmation Required**:\nYou have selected **{len(non_safe_selected)} migrations** classified as `🟡 Check Growth` or `🔴 High Risk`.")
                    st.markdown("Altering column types on growing tables can cause insert errors if future data exceeds these bounds.")
                    st.markdown("**Potentially Growing Columns Selected:**")
                    for stmt, s_obj, t_nm in non_safe_selected[:6]:
                        st.markdown(f"* `{t_nm}.{s_obj['column']}`: `{s_obj['current_type']}` → `{s_obj['suggested_type']}`")
                    if len(non_safe_selected) > 6:
                        st.caption(f"... +{len(non_safe_selected) - 6} more")
                    
                    user_conf = st.checkbox("I understand the growth risks and have backed up my database.", key="batch_db_conf_chk")
                    if st.button("🚀 Proceed & Apply All Migrations", type="primary", disabled=not user_conf, key="batch_db_proceed_btn"):
                        if not is_test_db:
                            st.session_state.is_test_db = True
                        errors = []
                        p_bar = st.progress(0, text="Applying database-wide type optimizations...")
                        for i, stmt in enumerate(selected_sqls_only):
                            p_bar.progress((i + 1) / len(selected_sqls_only), text=f"Executing fix {i+1}/{len(selected_sqls_only)}…")
                            _, err, _ = execute_query(engine, stmt, database=selected_db)
                            if err:
                                errors.append(f"{stmt} -> {err}")
                            else:
                                st.session_state.executed_fixes.add(stmt)
                        p_bar.empty()
                        if errors:
                            for e in errors:
                                st.error(e)
                        else:
                            st.success(f"🎉 Successfully applied {len(selected_sqls_only)} data type optimizations across the database!")
                            results = scan_all_tables_for_types(engine, selected_db, sample_limit=1500, deep_verify=True)
                            st.session_state[db_cache_key] = results
                            st.session_state[db_sql_cache_key] = generate_database_type_migration_script(results, dialect=dialect, database=selected_db, ai_audit_map=db_ai_audit_map)
                            st.rerun()
            else:
                if is_test_db:
                    btn_lbl = f"⚡ Apply Selected Fixes ({len(selected_sqls_only)})" if selected_sqls_only else "⚡ Apply Selected Fixes"
                    do_apply = st.button(btn_lbl, type="primary", use_container_width=True, key="apply_db_wide_types", disabled=not selected_sqls_only)
                else:
                    btn_lbl = f"🔓 Enable Execution & Apply Fixes ({len(selected_sqls_only)})" if selected_sqls_only else "🔓 Enable Execution & Apply Fixes"
                    do_apply = st.button(btn_lbl, type="primary", use_container_width=True, key="unlock_and_apply_db_wide", disabled=not selected_sqls_only)
                    if do_apply:
                        st.session_state.is_test_db = True

                if do_apply and selected_sqls_only:
                    errors = []
                    p_bar = st.progress(0, text="Applying database-wide type optimizations...")
                    for i, stmt in enumerate(selected_sqls_only):
                        p_bar.progress((i + 1) / len(selected_sqls_only), text=f"Executing fix {i+1}/{len(selected_sqls_only)}…")
                        _, err, _ = execute_query(engine, stmt, database=selected_db)
                        if err:
                            errors.append(f"{stmt} -> {err}")
                        else:
                            st.session_state.executed_fixes.add(stmt)
                    p_bar.empty()
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        st.success(f"🎉 Successfully applied {len(selected_sqls_only)} data type optimizations across the database!")
                        results = scan_all_tables_for_types(engine, selected_db, sample_limit=1500, deep_verify=True)
                        st.session_state[db_cache_key] = results
                        st.session_state[db_sql_cache_key] = generate_database_type_migration_script(results, dialect=dialect, database=selected_db, ai_audit_map=db_ai_audit_map)
                        st.rerun()

        with b_c2:
            if not is_test_db:
                st.caption("🔒 **Execution Mode is OFF** (Read-Only Guardrail). Clicking above will enable execution mode and apply your selected migrations directly.")
            else:
                st.caption(f"⚡ **Execution Mode is ACTIVE**. Click above to apply all {len(selected_sqls_only)} selected DDL migrations.")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: Single Table Deep Inspector
# ══════════════════════════════════════════════════════════════════════════════
with tab_single:
    st.markdown("### 📋 Single Table Deep Inspector")
    st.caption("Inspect and fine-tune individual column profiling for a specific table.")

    col_sel, col_scan, col_deep = st.columns([3, 1, 2])
    with col_sel:
        target_table = st.selectbox("Select table to profile", tables, key="single_tbl_select")
    with col_scan:
        sample_limit = st.selectbox("Sample rows", [500, 1000, 2500, 5000], index=1, key="single_sample_limit")
    with col_deep:
        st.write("")
        deep_verify = st.checkbox("🔬 Full-Table Deep Aggregates (100% Tested)", value=True, key="single_deep_verify")

    if target_table:
        with st.spinner(f"Profiling columns on `{target_table}`…"):
            try:
                single_suggs = profile_table_columns(engine, selected_db, target_table, sample_limit=sample_limit, deep_verify=deep_verify)
                stats = sr.get_table_stats(engine, selected_db, target_table)
            except Exception as e:
                st.error(f"Profiling error: {e}")
                single_suggs = []
                stats = {}

        t_saved_bytes = sum(s.get("est_total_saved_bytes", 0) for s in single_suggs)
        b_saved_row = sum(s.get("saved_bytes_per_row", 0) for s in single_suggs)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            render_metric_card("Optimization Targets", str(len(single_suggs)), f"On {target_table}", badge="Suggestions", badge_type="info" if single_suggs else "success")
        with c2:
            render_metric_card("Bytes / Row Saved", f"{b_saved_row} B", "Per record footprint", badge="Per Row", badge_type="success")
        with c3:
            render_metric_card("Total Table Savings", format_bytes(t_saved_bytes), f"Across {stats.get('approx_rows', 0):,} rows", badge="Storage Saved", badge_type="success" if t_saved_bytes > 0 else "info")
        with c4:
            render_metric_card("Current Table Size", format_bytes(stats.get("data_size_bytes", 0)), f"{stats.get('approx_rows', 0):,} rows", badge="Current", badge_type="purple")

        st.divider()

        if not single_suggs:
            st.success(f"🎉 **Great Data Types!** All columns on `{target_table}` are optimal with no downcasting, truncation, or conversion opportunities detected.")
        else:
            # AI Semantic Audit Toolbar
            ai_c1, ai_c2 = st.columns([3, 1])
            with ai_c1:
                st.markdown("#### Actionable Recommendations Checklist")
            with ai_c2:
                if st.button("🤖 Run AI Semantic Audit", use_container_width=True, key=f"ai_audit_btn_{target_table}"):
                    sample_rows = []
                    try:
                        with engine.connect() as conn:
                            t_q = f"`{selected_db}`.`{target_table}`" if dialect == "mysql" and selected_db else (f"`{target_table}`" if dialect == "mysql" else f'"{target_table}"')
                            s_res = conn.execute(text(f"SELECT * FROM {t_q} LIMIT 3")).mappings().all()
                            sample_rows = [dict(r) for r in s_res]
                    except Exception:
                        pass
                    prompt = build_type_audit_prompt(dialect, target_table, single_suggs, sample_rows=sample_rows)
                    with st.spinner("Consulting Local AI Schema Auditor…"):
                        ai_raw = ask(cfg, prompt)
                        st.session_state[f"ai_audit_raw_{target_table}"] = ai_raw
                        st.session_state[f"ai_audit_parsed_{target_table}"] = parse_ai_type_audit(ai_raw)
                    st.rerun()

            if f"ai_audit_raw_{target_table}" in st.session_state:
                with st.expander("📖 Full AI Semantic Audit Report", expanded=False):
                    st.markdown(st.session_state[f"ai_audit_raw_{target_table}"])

            ai_audit_map = st.session_state.get(f"ai_audit_parsed_{target_table}", {})

            # Selection Toolbar for Single Table
            s_tb1, s_tb2, s_tb3 = st.columns([1, 1, 3])
            if s_tb1.button("🟢 Select Safe Only", key=f"single_sel_safe_{target_table}", use_container_width=True):
                for idx, s in enumerate(single_suggs):
                    st.session_state[f"single_chk_{target_table}_{idx}"] = (s.get("risk_level") == "SAFE")
                st.rerun()

            if s_tb2.button("☑️ Select All", key=f"single_sel_all_{target_table}", use_container_width=True):
                for idx in range(len(single_suggs)):
                    st.session_state[f"single_chk_{target_table}_{idx}"] = True
                st.rerun()

            if s_tb3.button("⬜ Deselect All", key=f"single_desel_all_{target_table}", use_container_width=True):
                for idx in range(len(single_suggs)):
                    st.session_state[f"single_chk_{target_table}_{idx}"] = False
                st.rerun()

            selected_single_items = []

            for i, s in enumerate(single_suggs):
                sql_stmt = s["sql"]
                is_applied = sql_stmt in st.session_state.executed_fixes
                col_name_lower = s["column"].lower()
                col_audit = ai_audit_map.get(col_name_lower, {})
                ai_status = col_audit.get("status", "")
                ai_analysis = col_audit.get("analysis", "")

                with st.container():
                    r_c1, r_c2 = st.columns([1, 15])
                    with r_c1:
                        if is_applied:
                            st.markdown("✅")
                        else:
                            chk_single_key = f"single_chk_{target_table}_{i}"
                            if chk_single_key not in st.session_state:
                                st.session_state[chk_single_key] = s.get("default_checked", False) if ai_status != "CAUTION" else False
                            is_checked = st.checkbox("", key=chk_single_key)
                            if is_checked:
                                selected_single_items.append((sql_stmt, s))

                    with r_c2:
                        badge_html = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-info">{s.get("verification", "Verified")}</span>'
                        
                        # Add Risk Badge
                        r_level = s.get("risk_level", "SAFE")
                        if r_level == "SAFE":
                            badge_html += ' <span class="badge-pill badge-success">🟢 Safe & Confirmed</span>'
                        elif r_level == "MAYBE":
                            badge_html += ' <span class="badge-pill badge-warning">🟡 Check Growth</span>'
                        else:
                            badge_html += ' <span class="badge-pill badge-danger">🔴 High Risk / Sequence</span>'

                        # Add AI Badge
                        if ai_status == "APPROVED":
                            badge_html += ' <span class="badge-pill badge-success">🛡️ AI Approved</span>'
                        elif ai_status == "CAUTION":
                            badge_html += ' <span class="badge-pill badge-warning">⚠️ AI Caution</span>'
                        elif ai_status == "ALTERNATIVE":
                            badge_html += ' <span class="badge-pill badge-info">💡 AI Refinement</span>'

                        st.markdown(f"**🔧 `{s['column']}`**: `{s['current_type']}` → **`{s['suggested_type']}`** (Saves {s['formatted_savings']}) {badge_html}", unsafe_allow_html=True)
                        st.caption(f"**Reason**: {s['reason']}")
                        
                        code_c1, code_c2 = st.columns([12, 3])
                        with code_c1:
                            st.code(sql_stmt, language="sql")
                        with code_c2:
                            st.write("")
                            if is_applied:
                                st.button("✅ Applied", key=f"btn_run_single_tbl_{target_table}_{i}", disabled=True, use_container_width=True)
                            else:
                                if r_level != "SAFE":
                                    with st.popover("⚠️ Verify & Run", use_container_width=True):
                                        st.warning(f"**Growth Check Required for `{target_table}.{s['column']}`**:\nAltering column type may cause errors if future application inserts exceed `{s['suggested_type']}`.")
                                        conf_single = st.checkbox("I have verified future volume.", key=f"chk_conf_single_{target_table}_{i}")
                                        if st.button("Confirm & Apply Fix", type="primary", disabled=not conf_single, key=f"btn_conf_run_single_{target_table}_{i}"):
                                            if not is_test_db:
                                                st.session_state.is_test_db = True
                                            with st.spinner(f"Applying fix on `{target_table}.{s['column']}`…"):
                                                _, err, _ = execute_query(engine, sql_stmt, database=selected_db)
                                                if err:
                                                    st.error(f"Error: {err}")
                                                else:
                                                    st.session_state.executed_fixes.add(sql_stmt)
                                                    st.success(f"✅ Updated `{target_table}.{s['column']}`!")
                                                    st.rerun()
                                else:
                                    run_lbl = "▶️ Run Fix" if is_test_db else "🔓 Run Fix"
                                    if st.button(run_lbl, key=f"btn_run_single_tbl_{target_table}_{i}", use_container_width=True):
                                        if not is_test_db:
                                            st.session_state.is_test_db = True
                                        with st.spinner(f"Applying fix on `{target_table}.{s['column']}`…"):
                                            _, err, _ = execute_query(engine, sql_stmt, database=selected_db)
                                            if err:
                                                st.error(f"Error: {err}")
                                            else:
                                                st.session_state.executed_fixes.add(sql_stmt)
                                                st.success(f"✅ Updated `{target_table}.{s['column']}`!")
                                                st.rerun()

                        if ai_analysis:
                            st.info(f"🤖 **AI Domain Analysis**: {ai_analysis}")

                    st.write("")

            # Single Table Bottom Execution Button Toolbar with Safety Modal
            st.write("")
            st.divider()

            non_safe_single = [item for item in selected_single_items if item[1].get("risk_level") != "SAFE"]
            selected_single_sqls = [item[0] for item in selected_single_items]

            sb_c1, sb_c2 = st.columns([2, 3])
            with sb_c1:
                if non_safe_single:
                    with st.popover(f"⚠️ Confirm & Apply Fixes ({len(selected_single_sqls)})", use_container_width=True):
                        st.warning(f"**Safety Confirmation Required**:\nYou have selected **{len(non_safe_single)} migrations** classified as `🟡 Check Growth` or `🔴 High Risk`.")
                        st.markdown("**Potentially Growing Columns Selected:**")
                        for stmt, s_obj in non_safe_single:
                            st.markdown(f"* `{s_obj['column']}`: `{s_obj['current_type']}` → `{s_obj['suggested_type']}`")
                        
                        user_conf_single = st.checkbox("I understand the growth risks and have backed up my database.", key=f"single_tbl_conf_chk_{target_table}")
                        if st.button("🚀 Proceed & Apply Fixes", type="primary", disabled=not user_conf_single, key=f"single_tbl_proceed_{target_table}"):
                            if not is_test_db:
                                st.session_state.is_test_db = True
                            errors = []
                            with st.spinner(f"Applying {len(selected_single_sqls)} fixes on `{target_table}`…"):
                                for stmt in selected_single_sqls:
                                    _, err, _ = execute_query(engine, stmt, database=selected_db)
                                    if err:
                                        errors.append(f"{stmt} -> {err}")
                                    else:
                                        st.session_state.executed_fixes.add(stmt)
                            if errors:
                                for e in errors:
                                    st.error(e)
                            else:
                                st.success(f"🎉 Successfully applied {len(selected_single_sqls)} data type optimizations on `{target_table}`!")
                                st.rerun()
                else:
                    if is_test_db:
                        btn_single_lbl = f"🔧 Apply Selected Type Fixes ({len(selected_single_sqls)})" if selected_single_sqls else "🔧 Apply Selected Type Fixes"
                        do_single_apply = st.button(btn_single_lbl, type="primary", use_container_width=True, key=f"apply_single_types_{target_table}", disabled=not selected_single_sqls)
                    else:
                        btn_single_lbl = f"🔓 Enable Execution & Apply Fixes ({len(selected_single_sqls)})" if selected_single_sqls else "🔓 Enable Execution & Apply Fixes"
                        do_single_apply = st.button(btn_single_lbl, type="primary", use_container_width=True, key=f"unlock_apply_single_{target_table}", disabled=not selected_single_sqls)
                        if do_single_apply:
                            st.session_state.is_test_db = True

                    if do_single_apply and selected_single_sqls:
                        errors = []
                        with st.spinner(f"Applying {len(selected_single_sqls)} fixes on `{target_table}`…"):
                            for stmt in selected_single_sqls:
                                _, err, _ = execute_query(engine, stmt, database=selected_db)
                                if err:
                                    errors.append(f"{stmt} -> {err}")
                                else:
                                    st.session_state.executed_fixes.add(stmt)
                        if errors:
                            for e in errors:
                                st.error(e)
                        else:
                            st.success(f"🎉 Successfully applied {len(selected_single_sqls)} data type optimizations on `{target_table}`!")
                            st.rerun()

            with sb_c2:
                if not is_test_db:
                    st.caption("🔒 **Execution Mode is OFF** (Read-Only Guardrail). Clicking above will enable execution mode and apply your selected migrations directly.")
                else:
                    st.caption(f"⚡ **Execution Mode is ACTIVE**. Click above to apply all {len(selected_single_sqls)} selected DDL migrations.")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Full Database Migration Script
# ══════════════════════════════════════════════════════════════════════════════
with tab_script:
    st.markdown("### 📜 Full Database Migration Script Generator")
    st.caption("Generates a combined, production-safe SQL DDL script for all recommended data type optimizations across the entire schema.")

    all_db_suggs_for_script = st.session_state.get(db_cache_key)

    if not all_db_suggs_for_script:
        st.info("Run the database scan in Tab 1 to generate the full database migration script.")
    else:
        db_ai_audit_map = st.session_state.get(db_ai_cache_key, {})
        has_ai_audit = bool(db_ai_audit_map)

        scr_c1, scr_c2 = st.columns([3, 1])
        with scr_c1:
            filter_mode = st.radio(
                "Script Generation Mode",
                ["🛡️ AI-Approved Safe Migrations Only", "🌐 All Recommended Migrations"],
                horizontal=True,
                index=0 if has_ai_audit else 1,
                help="AI-Approved mode excludes any column flagged with Caution by the Local AI Auditor."
            )
        with scr_c2:
            st.write("")
            ai_approved_only = (filter_mode == "🛡️ AI-Approved Safe Migrations Only")

        script_content = generate_database_type_migration_script(
            all_db_suggs_for_script,
            dialect=dialect,
            database=selected_db,
            ai_audit_map=db_ai_audit_map,
            ai_approved_only=ai_approved_only,
        )

        st.code(script_content, language="sql")
        st.download_button(
            "⬇ Download database_type_migrations.sql",
            script_content,
            file_name=f"{selected_db}_type_migrations.sql",
            mime="text/plain",
            use_container_width=True,
        )
