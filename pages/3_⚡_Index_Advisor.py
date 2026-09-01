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
from app.db.performance_analyzer import (
    fetch_live_slow_queries,
    parse_uploaded_slow_query_log,
    recommend_indexes_for_slow_queries,
)
from app.db.rollback_manager import (
    record_index_change,
    get_index_change_history,
    clear_index_change_history,
    generate_consolidated_rollback_script,
    infer_rollback_sql,
)

st.set_page_config(page_title="Index Advisor — SQL Helper", page_icon="⚡", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("⚡ Dual-Engine Index Advisor & Performance Workbench")
st.caption("Deterministic static rules + live slow query log ingestion + Local AI deep reasoning with catalog anti-hallucination verification.")

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

tab_global, tab_table, tab_slow, tab_ai, tab_script = st.tabs([
    "🌐 Database Issues & Fixes",
    "📋 Table Inspector",
    "🐢 Slow Queries & Traffic Advisor",
    "🤖 Local AI Deep Advisor & Verifier",
    "📜 Batch Migration Script"
])

all_actions = [item["action"] for items in global_findings.values() for item in items if item.get("action")]

# ── Tab 1: Database-Wide Scan ────────────────────────────────────────────────
with tab_global:
    st.markdown("### 🌐 Static Ground-Truth Index Findings")

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
                    "Columns": item.get("columns", []),
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
                st.session_state[f"chk_issue_{i}"] = True
            st.rerun()

        if t1_col2.button("⬜ Deselect All", key="static_desel_all", use_container_width=True):
            for i in range(len(all_issues)):
                st.session_state[f"chk_issue_{i}"] = False
            st.rerun()

        selected_sqls = []
        selected_issue_objs = []

        for i, row in enumerate(all_issues):
            action_sql = row["Action SQL"]
            if not action_sql:
                continue

            is_applied = action_sql in st.session_state.executed_fixes

            with st.container():
                r_col1, r_col2 = st.columns([1, 15])
                with r_col1:
                    if is_applied:
                        st.markdown("✅")
                    else:
                        chk_k = f"chk_issue_{i}"
                        if chk_k not in st.session_state:
                            st.session_state[chk_k] = True
                        is_checked = st.checkbox("", key=chk_k)
                        if is_checked:
                            selected_sqls.append(action_sql)
                            selected_issue_objs.append(row)
                with r_col2:
                    badge_html = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-warning">{row["Category"]}</span>'
                    st.markdown(f"**`{row['Table']}`** — `{row['Index']}` {badge_html}", unsafe_allow_html=True)
                    st.caption(row["Reason"])
                    st.code(action_sql, language="sql")
                st.write("")

        # Bottom Execution Toolbar
        st.write("")
        st.divider()
        b_c1, b_c2 = st.columns([2, 3])
        with b_c1:
            if is_test_db:
                btn_lbl = f"⚡ Apply Selected Fixes ({len(selected_sqls)})" if selected_sqls else "⚡ Apply Selected Fixes"
                do_apply = st.button(btn_lbl, type="primary", use_container_width=True, key="apply_static_fixes", disabled=not selected_sqls)
            else:
                btn_lbl = f"🔓 Enable Execution & Apply Fixes ({len(selected_sqls)})" if selected_sqls else "🔓 Enable Execution & Apply Fixes"
                do_apply = st.button(btn_lbl, type="primary", use_container_width=True, key="unlock_apply_static", disabled=not selected_sqls)
                if do_apply:
                    st.session_state.is_test_db = True

            if do_apply and selected_sqls:
                errors = []
                with st.spinner("Applying selected index modifications…"):
                    for stmt, row_obj in zip(selected_sqls, selected_issue_objs):
                        _, err, _ = execute_query(engine, stmt, database=selected_db)
                        if err:
                            errors.append(f"{stmt} -> {err}")
                        else:
                            st.session_state.executed_fixes.add(stmt)
                            # Record rollback history
                            act_type, rb_sql, desc = infer_rollback_sql(
                                dialect, selected_db, stmt,
                                table=row_obj.get("Table"),
                                columns=row_obj.get("Columns"),
                                index_name=row_obj.get("Index"),
                            )
                            record_index_change(selected_db, row_obj.get("Table"), act_type, stmt, rb_sql, desc)

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    st.success(f"🎉 Successfully applied {len(selected_sqls)} index modifications! 🛡️ Safety rollback scripts have been recorded in the Backups section below.")
                    st.toast("🛡️ Rollback script available in Backups section!", icon="🛡️")
                    st.rerun()

        with b_c2:
            if not is_test_db:
                st.caption("🔒 **Execution Mode is OFF** (Read-Only Guardrail). Clicking above will enable execution mode and apply selected index modifications.")
            else:
                st.caption(f"⚡ **Execution Mode is ACTIVE**. Click above to apply all {len(selected_sqls)} selected DDL statements.")

# ── Tab 2: Single Table Inspector ───────────────────────────────────────────
with tab_table:
    st.markdown("### 📋 Table Index Inspector")
    target_table = st.selectbox("Select table to inspect", tables, key="inspect_tbl")

    try:
        existing_indexes = sr.get_indexes(engine, selected_db, target_table)
        fks = sr.get_foreign_keys(engine, selected_db, target_table)
    except Exception as e:
        st.error(f"Error reading table indexes: {e}")
        existing_indexes = []
        fks = []

    st.markdown(f"#### Existing Indexes on `{target_table}` ({len(existing_indexes)})")
    if existing_indexes:
        idx_rows = []
        for idx in existing_indexes:
            idx_rows.append({
                "Index Name": idx["name"],
                "Unique": "✓" if idx.get("unique") else "",
                "Type": idx.get("type", "BTREE"),
                "Columns": ", ".join(idx.get("columns", [])),
            })
        st.dataframe(pd.DataFrame(idx_rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"No indexes found on `{target_table}`.")

    # Table-Specific Static Issues
    t_dups = [d for d in global_findings["duplicates"] if d.get("table") == target_table]
    t_reds = [r for r in global_findings["redundant"] if r.get("table") == target_table]
    t_fks = [f for f in global_findings["missing_fk"] if f.get("table") == target_table]

    if t_dups or t_reds or t_fks:
        st.markdown("#### Table-Specific Recommended Fixes")
        for fix in t_dups + t_reds + t_fks:
            st.warning(f"**[{fix.get('type')}]** {fix.get('reason')}")
            st.code(fix.get("action", ""), language="sql")

# ── Tab 3: Slow Queries & Traffic-Driven Advisor ────────────────────────────
with tab_slow:
    st.markdown("### 🐢 Slow Query Traffic & Performance Schema Ingestion")
    st.caption("Ingest real-time slow queries and full table scans from server performance schema or upload a `slow_query.log` file to synthesize composite indexes.")

    sq_c1, sq_c2 = st.columns([3, 2])
    with sq_c1:
        st.markdown("#### 1. Live Server Performance Ingestion")
        live_btn = st.button("🔄 Pull Live Slow Queries from Server", type="primary", key="btn_pull_live_slow")
    with sq_c2:
        st.markdown("#### 2. Upload `slow_query.log` File")
        uploaded_log = st.file_uploader("Drop MySQL Slow Query Log (.log, .txt)", type=["log", "txt"], key="upload_slow_log")

    slow_queries_data = []

    if live_btn or "cached_slow_queries" in st.session_state:
        if live_btn:
            with st.spinner("Querying server performance schema / sys table scans…"):
                slow_queries_data = fetch_live_slow_queries(engine, selected_db, limit=20)
                st.session_state.cached_slow_queries = slow_queries_data
        else:
            slow_queries_data = st.session_state.get("cached_slow_queries", [])

    if uploaded_log:
        raw_text = uploaded_log.read().decode("utf-8", errors="ignore")
        parsed_queries = parse_uploaded_slow_query_log(raw_text)
        slow_queries_data = parsed_queries + slow_queries_data
        st.success(f"📥 Successfully parsed {len(parsed_queries)} slow query entries from uploaded log!")

    if not slow_queries_data:
        st.info("💡 Click **'🔄 Pull Live Slow Queries'** or upload a slow query log file above to analyze real-world traffic patterns.")
    else:
        st.markdown(f"#### 🔍 Ingested Slow Queries ({len(slow_queries_data)})")
        df_slow = pd.DataFrame([
            {
                "Query": q["query"][:80] + ("…" if len(q["query"]) > 80 else ""),
                "Latency": q.get("total_latency", "N/A"),
                "Exec Count": q.get("exec_count", 1),
                "Full Scan": "⚠️ YES" if q.get("full_table_scan") else "NO",
                "Avg Examined": f"{q.get('rows_examined_avg', 0):,.0f}",
                "Avg Sent": f"{q.get('rows_sent_avg', 0):,.0f}",
                "Inefficiency": f"{q.get('inefficiency_ratio', 1.0)}x",
                "Source": q.get("source", "Server"),
            }
            for q in slow_queries_data
        ])
        st.dataframe(df_slow, use_container_width=True, hide_index=True)

        # Generate Traffic-Driven Index Recommendations
        st.markdown("#### ⚡ Traffic-Driven Composite Index Recommendations")
        traffic_recs = recommend_indexes_for_slow_queries(slow_queries_data, dialect=dialect, existing_tables=tables)

        if not traffic_recs:
            st.info("No composite index opportunities identified from the current slow queries sample.")
        else:
            selected_traffic_sqls = []
            for idx, tr in enumerate(traffic_recs):
                t_sql = tr["sql"]
                is_applied = t_sql in st.session_state.executed_fixes

                with st.container():
                    r1, r2 = st.columns([1, 15])
                    with r1:
                        if is_applied:
                            st.markdown("✅")
                        else:
                            chk_t_key = f"chk_traffic_idx_{idx}"
                            if chk_t_key not in st.session_state:
                                st.session_state[chk_t_key] = True
                            is_checked = st.checkbox("", key=chk_t_key)
                            if is_checked:
                                selected_traffic_sqls.append((t_sql, tr))

                    with r2:
                        b_badge = '<span class="badge-pill badge-success">✅ APPLIED</span>' if is_applied else f'<span class="badge-pill badge-info">⚡ {tr["estimated_gain"]}</span>'
                        st.markdown(f"**Table `{tr['table']}`** — `{tr['index_name']}` ({', '.join(tr['columns'])}) {b_badge}", unsafe_allow_html=True)
                        st.caption(f"{tr['reason']} | Trigger: `{tr['trigger_query']}`")
                        
                        code_c1, code_c2 = st.columns([12, 3])
                        with code_c1:
                            st.code(t_sql, language="sql")
                        with code_c2:
                            st.write("")
                            if is_applied:
                                st.button("✅ Applied", key=f"btn_traffic_run_{idx}", disabled=True, use_container_width=True)
                            else:
                                t_run_lbl = "▶️ Run Index" if is_test_db else "🔓 Run Index"
                                if st.button(t_run_lbl, key=f"btn_traffic_run_{idx}", use_container_width=True):
                                    if not is_test_db:
                                        st.session_state.is_test_db = True
                                    with st.spinner(f"Creating composite index on `{tr['table']}`…"):
                                        _, err, _ = execute_query(engine, t_sql, database=selected_db)
                                        if err:
                                            st.error(f"Error: {err}")
                                        else:
                                            st.session_state.executed_fixes.add(t_sql)
                                            # Record rollback
                                            act_type, rb_sql, desc = infer_rollback_sql(
                                                dialect, selected_db, t_sql,
                                                table=tr["table"], columns=tr["columns"], index_name=tr["index_name"]
                                            )
                                            record_index_change(selected_db, tr["table"], act_type, t_sql, rb_sql, desc)
                                            st.success(f"✅ Created `{tr['index_name']}`! 🛡️ Rollback script saved in Backups section below.")
                                            st.toast("🛡️ Rollback script saved in Backups section!", icon="🛡️")
                                            st.rerun()
                    st.write("")

# ── Tab 4: Local AI Deep Advisor ────────────────────────────────────────────
with tab_ai:
    st.markdown("### 🤖 Local AI Index Architect (Ollama / Cloud LLM)")
    st.caption("Deep reasoning model analyzes query patterns, composite column selectivity, and real-world write penalties.")

    ai_target_table = st.selectbox("Select table for AI deep reasoning", tables, key="ai_target_tbl")

    if st.button(f"🧠 Consult AI Architect for `{ai_target_table}`", type="primary", use_container_width=True):
        with st.spinner("AI Architect is evaluating column selectivity and query patterns…"):
            try:
                cols = sr.get_columns(engine, selected_db, ai_target_table)
                idxs = sr.get_indexes(engine, selected_db, ai_target_table)
                t_stats = sr.get_table_stats(engine, selected_db, ai_target_table)
                sample_df = get_page(engine, selected_db, ai_target_table, page=0, page_size=5)
                sample_rows = sample_df.to_dict(orient="records") if not sample_df.empty else []

                prompt = build_index_prompt(dialect, ai_target_table, cols, idxs, t_stats, sample_rows)
                raw_response = ask(cfg, prompt)
                st.session_state[f"ai_idx_response_{ai_target_table}"] = raw_response
            except Exception as e:
                st.error(f"AI Index Advisor error: {e}")

    if f"ai_idx_response_{ai_target_table}" in st.session_state:
        raw_response = st.session_state[f"ai_idx_response_{ai_target_table}"]
        verified_sqls, unverified_sqls = extract_and_verify_sql_statements(raw_response, engine, selected_db)

        st.divider()

        if verified_sqls:
            st.markdown(f"#### 🛡️ AI Recommended Actions for `{ai_target_table}` (Catalog Verified)")
            selected_ai_sqls = []
            for idx, sql_stmt in enumerate(verified_sqls):
                is_app = sql_stmt in st.session_state.executed_fixes
                r_c1, r_c2 = st.columns([1, 15])
                with r_c1:
                    if is_app:
                        st.markdown("✅")
                    else:
                        if st.checkbox("", value=True, key=f"ai_chk_{ai_target_table}_{idx}"):
                            selected_ai_sqls.append(sql_stmt)
                with r_c2:
                    st.code(sql_stmt, language="sql")
                st.write("")

            if is_test_db:
                btn_ai_lbl = f"⚡ Apply Verified AI Index Fixes ({len(selected_ai_sqls)})" if selected_ai_sqls else "⚡ Apply Verified Fixes"
                if st.button(btn_ai_lbl, type="primary", use_container_width=True, key="apply_ai_indexes", disabled=not selected_ai_sqls):
                    errors = []
                    with st.spinner("Applying AI verified index modifications…"):
                        for stmt in selected_ai_sqls:
                            _, err, _ = execute_query(engine, stmt, database=selected_db)
                            if err:
                                errors.append(f"{stmt} -> {err}")
                            else:
                                st.session_state.executed_fixes.add(stmt)
                                act_type, rb_sql, desc = infer_rollback_sql(dialect, selected_db, stmt, table=ai_target_table)
                                record_index_change(selected_db, ai_target_table, act_type, stmt, rb_sql, desc)
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        st.success(f"🎉 Successfully applied {len(selected_ai_sqls)} index modifications! 🛡️ Safety rollback recorded in Backups below.")
                        st.toast("🛡️ Rollback script saved in Backups section!", icon="🛡️")
                        st.rerun()
            else:
                st.warning("⚠️ Enable **'Execution Mode'** in the sidebar to execute selected fixes directly on the database.")
        else:
            st.success("🎉 **Optimal Index Health**: The AI Architect verified that this table is well-indexed. No further index additions or drops are needed.")

        with st.expander("📖 Full AI Advisory Report", expanded=True):
            st.markdown(raw_response)

# ── Tab 5: Batch Migration Script ───────────────────────────────────────────
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

# ════════════════════════════════════════════════════════════════════════════
# 🛡️ Backups & Index Rollback Scripts Section (Bottom of Page)
# ════════════════════════════════════════════════════════════════════════════
st.write("")
st.markdown("---")
st.markdown("### 🛡️ Backups & Index Rollback Scripts")
st.caption("Every index change executed through SQL Helper is automatically logged below with date, time, action description, and its exact inverse SQL rollback DDL.")

rollback_history = get_index_change_history(selected_db)
consolidated_rollback_sql = generate_consolidated_rollback_script(selected_db)

rb_c1, rb_c2 = st.columns([3, 1])
with rb_c1:
    st.markdown(f"**Recorded Modifications on `{selected_db}`**: **{len(rollback_history)} operations**")
with rb_c2:
    if rollback_history:
        st.download_button(
            "⬇ Download Rollback Script (.sql)",
            consolidated_rollback_sql,
            file_name=f"{selected_db}_index_rollback.sql",
            mime="text/plain",
            use_container_width=True,
            key="btn_dl_all_rollback_sql",
        )

if not rollback_history:
    st.info("ℹ️ No index changes have been executed yet during this session. When you execute an index drop or creation above, its rollback script will automatically appear here.")
else:
    with st.expander(f"📜 View Complete Rollback Script ({len(rollback_history)} Operations)", expanded=False):
        st.code(consolidated_rollback_sql, language="sql")

    st.markdown("#### 📋 Detailed Change Log & 1-Click Rollback")
    for item in rollback_history:
        item_id = item["id"]
        with st.container():
            col_info_box, col_revert_btn = st.columns([12, 3])
            with col_info_box:
                action_badge = "🔴 DROPPED" if item["action_type"] == "DROP INDEX" else "🟢 CREATED"
                st.markdown(f"**`{item['timestamp']}`** | **Table `{item['table']}`** | {action_badge} — *{item['description']}*")
                st.caption(f"Executed: `{item['forward_sql']}`")
                st.code(item["rollback_sql"], language="sql")
            with col_revert_btn:
                st.write("")
                st.write("")
                rev_label = "⏪ Rollback Fix" if is_test_db else "🔓 Rollback Fix"
                if st.button(rev_label, key=f"btn_rb_{item_id}", use_container_width=True):
                    if not is_test_db:
                        st.session_state.is_test_db = True
                    with st.spinner(f"Reverting index change on `{item['table']}`…"):
                        _, rb_err, _ = execute_query(engine, item["rollback_sql"], database=selected_db)
                        if rb_err:
                            st.error(f"Rollback error: {rb_err}")
                        else:
                            st.success(f"🎉 Successfully restored index state on `{item['table']}`!")
                            # Remove from executed fixes if present
                            st.session_state.executed_fixes.discard(item["forward_sql"])
                            st.rerun()
            st.divider()

    if st.button("🗑️ Clear Rollback History", key="btn_clear_rollback_hist"):
        clear_index_change_history(selected_db)
        st.rerun()
