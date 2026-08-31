import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import execute_query
from app.ai.index_advisor import (
    scan_database_indexes,
    find_redundant_indexes,
    find_missing_fk_indexes,
    find_low_cardinality_indexes,
    build_index_prompt,
)
from app.ai.validator import extract_and_verify_sql_statements, verify_sql_against_schema
from app.ai.provider import ask

st.set_page_config(page_title="Index Advisor — SQL Helper", page_icon="⚡", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("⚡ Dual-Engine Index Advisor & Health Workbench")
st.caption("Deterministic static rules + local AI deep reasoning, safeguarded by live catalog anti-hallucination verification.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to analyze indexes.")
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
            key="idx_db_switch",
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

# ── Phase 1: Global Deterministic Static Scan (Ground Truth) ────────────────
with st.spinner("Executing deterministic static index rules…"):
    try:
        global_findings = scan_database_indexes(engine, selected_db)
    except Exception as e:
        st.error(f"Error scanning indexes: {e}")
        global_findings = {"duplicates": [], "redundant": [], "missing_fk": [], "low_cardinality": [], "over_indexed": []}

total_dups = len(global_findings["duplicates"])
total_red = len(global_findings["redundant"])
total_fks = len(global_findings["missing_fk"])
total_low = len(global_findings["low_cardinality"])
total_issues = total_dups + total_red + total_fks + total_low

# ── Health Score KPI Cards ──────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    health_grade = "A" if total_issues == 0 else ("B" if total_issues <= 2 else ("C" if total_issues <= 5 else "D"))
    b_type = "success" if health_grade in ("A", "B") else "danger"
    render_metric_card("Index Health", f"Grade {health_grade}", f"{total_issues} factual issues", badge=f"Score: {health_grade}", badge_type=b_type)
with c2:
    render_metric_card("Exact Duplicates", str(total_dups), "Identical column sequence", badge="Duplicate", badge_type="danger" if total_dups > 0 else "success")
with c3:
    render_metric_card("Redundant Prefixes", str(total_red), "Covered by composite index", badge="Redundant", badge_type="warning" if total_red > 0 else "success")
with c4:
    render_metric_card("Missing FK Indexes", str(total_fks), "Unindexed foreign keys", badge="Missing FK", badge_type="danger" if total_fks > 0 else "success")
with c5:
    render_metric_card("Low Cardinality", str(total_low), "Single boolean/flag indexes", badge="Inefficient", badge_type="warning" if total_low > 0 else "success")

st.divider()

if "executed_fixes" not in st.session_state:
    st.session_state.executed_fixes = set()

tab_global, tab_table, tab_ai, tab_script = st.tabs([
    "🌐 Database Issues & Fixes",
    "📋 Table Inspector",
    "🤖 Local AI Deep Advisor & Verifier",
    "📜 Batch Migration Script"
])

# ── Tab 1: Database-Wide Scan ────────────────────────────────────────────────
with tab_global:
    st.markdown("### 🌐 Static Ground-Truth Index Findings")

    all_actions = [item["action"] for items in global_findings.values() for item in items if item.get("action")]

    if total_issues == 0:
        st.success("🎉 **Clean Bill of Health!** No duplicate, redundant, or missing foreign key indexes detected.")
    else:
        all_issues = []
        for cat, items in global_findings.items():
            for item in items:
                all_issues.append({
                    "Table": item.get("table", ""),
                    "Category": item.get("type", cat),
                    "Index": item.get("index", ""),
                    "Reason": item.get("reason", ""),
                    "Action SQL": item.get("action", ""),
                })

        df_issues = pd.DataFrame(all_issues)
        st.dataframe(df_issues[["Table", "Category", "Index", "Reason"]], use_container_width=True, hide_index=True)

        st.markdown("#### Select Fixes to Apply")

        # Select All / Deselect All Toolbar
        t1_col1, t1_col2, t1_col3 = st.columns([1, 1, 3])
        if t1_col1.button("☑️ Select All", key="static_sel_all", use_container_width=True):
            for i in range(len(all_issues)):
                st.session_state[f"static_chk_{i}"] = True
            st.rerun()

        if t1_col2.button("⬜ Deselect All", key="static_desel_all", use_container_width=True):
            for i in range(len(all_issues)):
                st.session_state[f"static_chk_{i}"] = False
            st.rerun()

        selected_static_sqls = []
        for i, issue in enumerate(all_issues):
            sql_stmt = issue["Action SQL"]
            is_applied = sql_stmt in st.session_state.executed_fixes

            with st.container():
                row_c1, row_c2 = st.columns([1, 15])
                with row_c1:
                    if is_applied:
                        st.markdown("✅")
                    else:
                        is_checked = st.checkbox("", value=True, key=f"static_chk_{i}")
                        if is_checked:
                            selected_static_sqls.append(sql_stmt)

                with row_c2:
                    cat_icon = "🗑️" if "DROP" in sql_stmt.upper() else "⚡"
                    badge_html = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-info">{issue["Category"]}</span>'
                    st.markdown(f"**{cat_icon} `{issue['Table']}`** — `{issue['Index']}` {badge_html}", unsafe_allow_html=True)
                    st.caption(issue["Reason"])
                    st.code(sql_stmt, language="sql")
                st.write("")

        # Bottom Batch Execution Button
        st.write("")
        if is_test_db:
            btn_label = f"⚡ Apply Selected Fixes Now ({len(selected_static_sqls)})" if selected_static_sqls else "⚡ Apply Selected Fixes Now"
            if st.button(btn_label, type="primary", use_container_width=True, key="apply_selected_static", disabled=not selected_static_sqls):
                errors = []
                with st.spinner(f"Executing {len(selected_static_sqls)} index modifications…"):
                    for sql in selected_static_sqls:
                        _, err, _ = execute_query(engine, sql, database=selected_db)
                        if err:
                            if "1091" in err or "Can't DROP" in err or "no such index" in err:
                                st.session_state.executed_fixes.add(sql)
                            else:
                                errors.append(f"{sql} -> {err}")
                        else:
                            st.session_state.executed_fixes.add(sql)
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    st.success(f"🎉 Successfully applied {len(selected_static_sqls)} index modifications!")
                    st.rerun()
        else:
            st.warning("⚠️ Enable **'Execution Mode'** in the sidebar to execute selected fixes directly on the database.")

# ── Tab 2: Table Inspector ──────────────────────────────────────────────────
with tab_table:
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        target_tbl = st.selectbox("Select table to inspect", tables, key="idx_tbl_select")

    if target_tbl:
        try:
            tbl_indexes = sr.get_indexes(engine, selected_db, target_tbl)
            tbl_fks = sr.get_foreign_keys(engine, selected_db, target_tbl)
            tbl_cols = sr.get_columns(engine, selected_db, target_tbl)
            tbl_stats = sr.get_table_stats(engine, selected_db, target_tbl)
        except Exception as e:
            st.error(f"Error reading table details: {e}")
            st.stop()

        st.markdown(f"#### Existing Indexes on `{target_tbl}` ({len(tbl_indexes)})")
        if tbl_indexes:
            idx_df = pd.DataFrame([
                {
                    "Name": idx["name"],
                    "Unique": "✓" if idx.get("unique") else "",
                    "Columns": ", ".join(idx.get("columns", [])),
                    "Type": idx.get("type", "BTREE"),
                }
                for idx in tbl_indexes
            ])
            st.dataframe(idx_df, use_container_width=True, hide_index=True)
        else:
            st.warning("No indexes found on this table.")

        local_issues = (
            find_redundant_indexes(tbl_indexes, target_tbl, dialect, selected_db)
            + find_missing_fk_indexes(tbl_indexes, tbl_fks, target_tbl, dialect, selected_db)
            + find_low_cardinality_indexes(tbl_indexes, tbl_cols, target_tbl, dialect, selected_db)
        )

        st.markdown("#### Table Findings & Actionable Fixes")
        if not local_issues:
            st.success(f"No deterministic index issues found on `{target_tbl}`.")
        else:
            selected_tbl_sqls = []
            for j, issue in enumerate(local_issues):
                sql_stmt = issue["action"]
                is_applied = sql_stmt in st.session_state.executed_fixes

                r1, r2 = st.columns([1, 15])
                with r1:
                    if is_applied:
                        st.markdown("✅")
                    else:
                        if st.checkbox("", value=True, key=f"tbl_chk_{target_tbl}_{j}"):
                            selected_tbl_sqls.append(sql_stmt)

                with r2:
                    icon = "🗑️" if "DROP" in sql_stmt.upper() else "⚡"
                    badge_html = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-warning">{issue["type"]}</span>'
                    st.markdown(f"**{icon} {issue['index']}** {badge_html}", unsafe_allow_html=True)
                    st.caption(issue["reason"])
                    st.code(sql_stmt, language="sql")
                st.write("")

            if is_test_db:
                if st.button(f"⚡ Apply Selected Fixes for `{target_tbl}` ({len(selected_tbl_sqls)})", type="primary", use_container_width=True, key=f"apply_tbl_btn_{target_tbl}", disabled=not selected_tbl_sqls):
                    for sql in selected_tbl_sqls:
                        _, err, _ = execute_query(engine, sql, database=selected_db)
                        if not err:
                            st.session_state.executed_fixes.add(sql)
                    st.success(f"Applied fixes for `{target_tbl}`!")
                    st.rerun()
            else:
                st.caption("⚠️ Enable **'Test Mode'** in sidebar to execute.")

# ── Tab 3: Local AI Advisor & Anti-Hallucination Verifier ───────────────────
with tab_ai:
    st.markdown("### 🤖 Local AI Deep Advisor & Verification Engine")
    st.caption("AI analyzes multi-predicate patterns and composite indexes. All generated DDL is verified against the live catalog before presentation.")

    ai_table = st.selectbox("Select table for AI deep analysis", tables, key="ai_idx_tbl")
    ai_btn = st.button("✨ Run AI Analysis & Catalog Verification", type="primary", use_container_width=True)

    if ai_btn and ai_table:
        if "executed_fixes" in st.session_state:
            st.session_state.executed_fixes.clear()
        cols = sr.get_columns(engine, selected_db, ai_table)
        idxs = sr.get_indexes(engine, selected_db, ai_table)
        fks = sr.get_foreign_keys(engine, selected_db, ai_table)
        stats = sr.get_table_stats(engine, selected_db, ai_table)
        static_iss = (
            find_redundant_indexes(idxs, columns=cols, table=ai_table, dialect=dialect, database=selected_db)
            + find_missing_fk_indexes(idxs, fks, ai_table, dialect, selected_db)
            + find_low_cardinality_indexes(idxs, cols, ai_table, dialect, selected_db)
        )

        prompt = build_index_prompt(dialect, ai_table, cols, idxs, fks, stats, static_iss)

        with st.spinner("Consulting Local AI Database Architect…"):
            response = ask(cfg, prompt)

        st.session_state.ai_last_index_response = response
        st.session_state.ai_last_table = ai_table

    if "ai_last_index_response" in st.session_state and st.session_state.get("ai_last_table") == ai_table:
        raw_response = st.session_state.ai_last_index_response
        
        # Verify all SQL statements in AI response against live schema (includes CREATE and DROP statements)
        verified_stmts = extract_and_verify_sql_statements(engine, raw_response, database=selected_db, target_table=ai_table)

        if verified_stmts:
            st.markdown("#### 🛡️ Verified Actionable AI Recommendations")
            st.caption("Check the optimizations you wish to apply, then click 'Apply Selected Fixes Now':")

            # Selection Toolbar
            s_col1, s_col2, s_col3 = st.columns([1, 1, 3])
            if s_col1.button("☑️ Select All Verified", key="ai_sel_all", use_container_width=True):
                for k, v_st in enumerate(verified_stmts):
                    v_sql = v_st.get("sql", "")
                    is_app = v_st.get("is_applied") or (v_sql in st.session_state.executed_fixes)
                    if v_st.get("is_valid") and not is_app:
                        st.session_state[f"ai_chk_{ai_table}_{k}"] = True
                    else:
                        st.session_state[f"ai_chk_{ai_table}_{k}"] = False
                st.rerun()

            if s_col2.button("⬜ Deselect All", key="ai_desel_all", use_container_width=True):
                for k in range(len(verified_stmts)):
                    st.session_state[f"ai_chk_{ai_table}_{k}"] = False
                st.rerun()

            selected_ai_sqls = []

            for k, val_stmt in enumerate(verified_stmts):
                sql = val_stmt.get("sql", "")
                is_valid = val_stmt.get("is_valid", True)
                is_applied = val_stmt.get("is_applied", False) or (sql in st.session_state.executed_fixes)
                badge = "✅ Fix Applied Successfully" if is_applied else val_stmt.get("badge", "🛡️ Verified by Catalog")
                badge_type = "success" if (is_applied or val_stmt.get("badge_type") == "success") else "danger"
                action_type = val_stmt.get("action_type", "")

                icon = "🗑️ DROP INDEX" if action_type == "DROP" or "DROP" in sql.upper() else "⚡ CREATE INDEX"
                if "ALTER" in sql.upper(): icon = "🔧 ALTER TABLE"

                with st.container():
                    c_chk, c_body = st.columns([1, 15])
                    with c_chk:
                        if is_applied:
                            st.markdown("✅")
                        else:
                            # Default checked only if valid and not applied
                            default_chk = st.session_state.get(f"ai_chk_{ai_table}_{k}", is_valid)
                            is_checked = st.checkbox("", value=default_chk, key=f"ai_chk_{ai_table}_{k}")
                            if is_checked:
                                selected_ai_sqls.append(sql)

                    with c_body:
                        b_class = "badge-success" if badge_type == "success" else "badge-danger"
                        st.markdown(f"**{icon}** <span class=\"badge-pill {b_class}\">{badge}</span>", unsafe_allow_html=True)
                        st.code(sql, language="sql")

                        if is_applied:
                            st.caption("✅ This index modification has already been executed on the live database.")
                        elif val_stmt.get("issues"):
                            for iss in val_stmt["issues"]:
                                st.error(f"⚠️ {iss}")

                    st.write("")

            # Bottom Batch Execution Button for AI Recommendations
            st.write("")
            if is_test_db:
                btn_label = f"⚡ Apply Selected Fixes Now ({len(selected_ai_sqls)})" if selected_ai_sqls else "⚡ Apply Selected Fixes Now"
                if st.button(btn_label, type="primary", use_container_width=True, key=f"apply_selected_ai_{ai_table}", disabled=not selected_ai_sqls):
                    errors = []
                    with st.spinner(f"Executing {len(selected_ai_sqls)} selected modifications…"):
                        for stmt in selected_ai_sqls:
                            _, err, _ = execute_query(engine, stmt, database=selected_db)
                            if err:
                                # If MySQL Error 1091 (index does not exist / already dropped), handle gracefully as clean
                                if "1091" in err or "Can't DROP" in err or "no such index" in err:
                                    st.session_state.executed_fixes.add(stmt)
                                else:
                                    errors.append(f"{stmt} -> {err}")
                            else:
                                st.session_state.executed_fixes.add(stmt)
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        st.success(f"🎉 Successfully applied {len(selected_ai_sqls)} index modifications!")
                        st.rerun()
            else:
                st.warning("⚠️ Enable **'Execution Mode'** in the sidebar to execute selected fixes directly on the database.")
        else:
            st.success("🎉 **Optimal Index Health**: The AI Architect verified that this table is well-indexed. No further index additions or drops are needed.")

        with st.expander("📖 Full AI Advisory Report", expanded=True):
            st.markdown(raw_response)

# ── Tab 4: Batch Migration Script ───────────────────────────────────────────
with tab_script:
    st.markdown("### 📜 Batch Index Optimization Script")
    st.caption("Combined DDL script for dropping redundant indexes and creating missing foreign key indexes.")

    if all_actions:
        migration_sql = (
            f"-- ========================================================\n"
            f"-- SQL Helper: Automated Index Optimization Script\n"
            f"-- Dialect: {dialect.upper()} | Database: {selected_db}\n"
            f"-- ========================================================\n\n"
            + "\n\n".join(all_actions)
        )
        st.code(migration_sql, language="sql")
        st.download_button(
            "⬇ Download index_optimizations.sql",
            migration_sql,
            file_name="index_optimizations.sql",
            mime="text/plain",
            use_container_width=True,
        )
    else:
        st.info("No index modifications needed.")
