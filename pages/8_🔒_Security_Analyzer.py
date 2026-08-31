import streamlit as st
import pandas as pd
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.db.data_reader import execute_query
from app.ai.security_analyzer import run_static_checks, build_security_prompt
from app.ai.provider import ask
from app.ai.validator import extract_and_verify_sql_statements

st.set_page_config(page_title="Security Analyzer — SQL Helper", page_icon="🔒", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🔒 Database Security & Integrity Analyzer")
st.caption("Scan for unencrypted sensitive columns, missing primary keys, dynamic SQL injection patterns, and privilege risks.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to run security analysis.")
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
            key="sec_db_switch",
        )
        if sel_db != current_db:
            st.session_state.selected_database = sel_db
            persist_active_database(sel_db)
            st.rerun()

selected_db = st.session_state.get("selected_database", current_db)

col_run, col_ai = st.columns([2, 2])
with col_run:
    run_btn = st.button("🔍 Run Full Security Audit", type="primary", use_container_width=True)
with col_ai:
    include_ai = st.checkbox("Include Local AI Deep Security Reasoning", value=True)

if run_btn or st.session_state.get("security_last_findings"):
    if run_btn:
        with st.spinner("Inspecting database catalog and permissions…"):
            try:
                tables = sr.list_tables(engine, selected_db)
                columns_by_table = {t: sr.get_columns(engine, selected_db, t) for t in tables}
                indexes_by_table = {t: sr.get_indexes(engine, selected_db, t) for t in tables}
                fk_by_table = {t: sr.get_foreign_keys(engine, selected_db, t) for t in tables}
                procedures = sr.list_procedures(engine, selected_db)
                procedure_bodies = {p: sr.get_procedure_body(engine, selected_db, p) for p in procedures}
                grants = sr.get_grants(engine)
            except Exception as e:
                st.error(f"Error gathering schema: {e}")
                st.stop()

        findings = run_static_checks(
            tables, columns_by_table, indexes_by_table, fk_by_table, procedure_bodies, grants,
            dialect=dialect, database=selected_db
        )
        findings.sort(key=lambda x: {"Critical": 0, "Warning": 1, "Info": 2}.get(x.get("severity", "Info"), 99))
        st.session_state.security_last_findings = findings
        st.session_state.security_columns = columns_by_table
    else:
        findings = st.session_state.get("security_last_findings", [])
        columns_by_table = st.session_state.get("security_columns", {})

    critical = [f for f in findings if f["severity"] == "Critical"]
    warnings = [f for f in findings if f["severity"] == "Warning"]
    actionable = [f for f in findings if f.get("action_sql")]

    c1, c2, c3 = st.columns(3)
    with c1:
        render_metric_card("Critical Risks", str(len(critical)), "Requires immediate attention", badge="Critical", badge_type="danger" if critical else "success")
    with c2:
        render_metric_card("Warnings", str(len(warnings)), "Integrity / performance flags", badge="Warning", badge_type="warning" if warnings else "success")
    with c3:
        render_metric_card("Actionable Fixes", str(len(actionable)), "Ready-to-apply remediations", badge="Remediations", badge_type="purple" if actionable else "info")

    st.markdown("### 📋 Static Security Findings & Remediation")

    if not findings:
        st.success("🎉 **No static security vulnerabilities detected!**")
    else:
        if actionable:
            b_col1, b_col2 = st.columns([2, 1])
            with b_col1:
                st.info(f"💡 Found **{len(actionable)} actionable security remediations** with verified SQL fixes.")
            with b_col2:
                if is_test_db:
                    if st.button("🔒 Apply All Actionable Fixes", type="primary", use_container_width=True, key="sec_apply_all"):
                        errors = []
                        for item in actionable:
                            _, err, _ = execute_query(engine, item["action_sql"], database=selected_db)
                            if err:
                                errors.append(f"{item['action_sql']} -> {err}")
                        if errors:
                            for e in errors:
                                st.error(e)
                        else:
                            st.success(f"Successfully applied {len(actionable)} security remediations!")
                            st.session_state.security_last_findings = None
                            st.rerun()
                else:
                    st.caption("⚠️ Enable **'Test Mode'** in sidebar to execute.")

        for idx, f in enumerate(findings):
            sev = f.get("severity", "Info")
            b_type = "danger" if sev == "Critical" else ("warning" if sev == "Warning" else "info")
            with st.expander(f"**[{sev.upper()}]** `{f.get('table', 'DB')}`: {f.get('category')}", expanded=(idx < 2)):
                st.write(f.get("message"))
                if f.get("action_sql"):
                    st.markdown("**Remediation SQL:**")
                    st.code(f["action_sql"], language="sql")
                    if is_test_db:
                        if st.button(f"▶ Apply Fix: {f.get('fix_label', 'Remediate')}", key=f"fix_sec_{idx}", type="primary"):
                            _, err, _ = execute_query(engine, f["action_sql"], database=selected_db)
                            if err:
                                st.error(f"Error: {err}")
                            else:
                                st.success("Remediation applied successfully!")
                                st.session_state.security_last_findings = None
                                st.rerun()
                    else:
                        st.caption("⚠️ Enable **'Test Mode'** in sidebar to execute.")

        df_findings = pd.DataFrame(findings)
        csv = df_findings.to_csv(index=False).encode()
        st.download_button("⬇ Download Security Audit CSV", csv, file_name="security_findings.csv", mime="text/csv")

    if include_ai and (run_btn or st.session_state.get("security_ai_response")):
        st.divider()
        st.markdown("### 🤖 Local AI Deep Security Reasoning")

        if run_btn or not st.session_state.get("security_ai_response"):
            schema_summary = "\n".join(
                f"  {tbl}: {', '.join(c['column'] + ' ' + c['type'] for c in cols[:8])}"
                for tbl, cols in columns_by_table.items()
            )
            prompt = build_security_prompt(dialect, schema_summary, findings)

            with st.spinner("Consulting Local AI Security Auditor…"):
                ai_response = ask(cfg, prompt)
                st.session_state.security_ai_response = ai_response
        else:
            ai_response = st.session_state.get("security_ai_response")

        st.markdown(ai_response)
