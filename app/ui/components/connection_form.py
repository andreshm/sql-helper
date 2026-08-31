import os
import time
from pathlib import Path
import streamlit as st
from app.db.connector import build_engine
from app.db.demo_db import generate_demo_database, DEMO_DB_PATH
from app.db.connections import (
    load_connections,
    save_connection,
    delete_connection,
    get_password,
    save_last_session,
    get_last_session,
    get_connection_by_id,
)
from app.config import load_config
from app.ai.provider import check_ollama_status

_DB_TYPES = ["sqlite", "mysql", "mariadb", "postgresql"]
_DEFAULT_PORTS = {"mysql": 3306, "mariadb": 3306, "postgresql": 5432, "sqlite": 0}
_TYPE_ICONS = {"sqlite": "🪶", "mysql": "🐬", "mariadb": "🦭", "postgresql": "🐘"}


def _do_connect(conn: dict, password: str = "", target_db: str = "") -> None:
    """Build engine and store in session state, persisting session."""
    active_db = target_db or conn.get("database", "") or ("main" if conn["type"] == "sqlite" else "")

    engine = build_engine(
        db_type=conn["type"],
        host=conn.get("host", ""),
        port=int(conn.get("port", 0) or 0),
        user=conn.get("user", ""),
        password=password,
        database=active_db,
        sqlite_path=conn.get("sqlite_path", ""),
    )
    st.session_state.engine = engine
    st.session_state.connection_info = conn.copy()
    st.session_state.active_conn_id = conn.get("id")
    st.session_state.selected_database = active_db or ("main" if conn["type"] == "sqlite" else None)
    st.session_state.selected_table = None

    # Persist session to pick up where left off
    save_last_session(
        conn_id=conn.get("id", ""),
        database=st.session_state.selected_database or "",
        tables=st.session_state.get("selected_tables", []),
    )


def _disconnect() -> None:
    engine = st.session_state.get("engine")
    if engine:
        try:
            engine.dispose()
        except Exception:
            pass
    st.session_state.engine = None
    st.session_state.connection_info = {}
    st.session_state.active_conn_id = None
    st.session_state.selected_database = None
    st.session_state.selected_table = None
    st.session_state.selected_tables = []
    save_last_session("", "", [])


def auto_restore_session_if_needed():
    """Attempt to restore the last active session on initial startup."""
    if st.session_state.get("engine") is not None:
        return

    if st.session_state.get("auto_reconnect_attempted"):
        return

    st.session_state.auto_reconnect_attempted = True
    last_sess = get_last_session()
    conn_id = last_sess.get("connection_id")
    last_db = last_sess.get("database", "")
    last_tables = last_sess.get("tables", [])

    saved_conns = load_connections()

    # If no active last_session, or if last_session was demo DB but user has real saved connections
    if (not conn_id or conn_id == "demo-ecommerce-db") and saved_conns:
        conn_dict = saved_conns[0]
        conn_id = conn_dict["id"]
        last_db = last_db if (last_db and last_db != "main") else conn_dict.get("database", "")

    if not conn_id:
        return

    try:
        if conn_id == "demo-ecommerce-db":
            demo_path = generate_demo_database(force_recreate=False)
            conn_dict = {
                "id": "demo-ecommerce-db",
                "name": "Demo E-Commerce DB",
                "type": "sqlite",
                "sqlite_path": demo_path,
                "database": "main",
            }
            _do_connect(conn_dict, target_db="main")
            st.session_state.is_test_db = True
            st.session_state.selected_tables = last_tables
            st.session_state.auto_restored = True

        else:
            conn_dict = get_connection_by_id(conn_id)
            if conn_dict:
                pwd = get_password(conn_id) if conn_dict.get("type") != "sqlite" else ""
                _do_connect(conn_dict, password=pwd, target_db=last_db)
                if last_db:
                    st.session_state.selected_database = last_db
                st.session_state.selected_tables = last_tables
                st.session_state.auto_restored = True
    except Exception:
        # If auto-reconnect fails (e.g. server offline), quietly reset
        pass


def _connection_form(existing: dict | None = None, form_key: str = "new"):
    is_edit = existing is not None
    e = existing or {}
    db_type_default = e.get("type", "sqlite")

    with st.form(key=f"conn_form_{form_key}"):
        name = st.text_input("Connection Name", value=e.get("name", ""))
        db_type = st.selectbox(
            "Database Engine",
            _DB_TYPES,
            index=_DB_TYPES.index(db_type_default) if db_type_default in _DB_TYPES else 0,
        )

        if db_type == "sqlite":
            sqlite_path = st.text_input(
                "SQLite File Path",
                value=e.get("sqlite_path", e.get("database", "demo_ecommerce.db")),
                help="Path to .db, .sqlite file, or :memory:",
            )
            host = ""
            port = 0
            user = ""
            password = ""
            database = "main"
        else:
            sqlite_path = ""
            col1, col2 = st.columns([3, 1])
            host = col1.text_input("Host", value=e.get("host", "localhost"))
            port = col2.number_input(
                "Port",
                value=int(e.get("port", _DEFAULT_PORTS.get(db_type, 3306))),
                min_value=1,
                max_value=65535,
            )
            user = st.text_input("User", value=e.get("user", "root" if "my" in db_type else "postgres"))
            password = st.text_input(
                "Password",
                value="",
                type="password",
                placeholder="leave blank to keep existing" if is_edit else "",
            )
            database = st.text_input("Default Database (optional)", value=e.get("database", ""))

        btn_cols = st.columns([2, 2, 1] if is_edit else [2, 2])
        save_btn = btn_cols[0].form_submit_button("💾 Save & Connect", type="primary", use_container_width=True)
        test_btn = btn_cols[1].form_submit_button("🔌 Test Only", use_container_width=True)
        del_btn = btn_cols[2].form_submit_button("🗑️", use_container_width=True) if is_edit else False

    conn = {
        "id": e.get("id", ""),
        "name": name.strip() or (sqlite_path if db_type == "sqlite" else host),
        "type": db_type,
        "host": host,
        "port": port,
        "user": user,
        "database": database,
        "sqlite_path": sqlite_path,
    }
    return save_btn, test_btn, del_btn, conn, password


def render_connection_sidebar():
    auto_restore_session_if_needed()

    with st.sidebar:
        st.markdown("## 🗄️ **SQL Helper**")
        st.caption("Universal Database Analyzer & Optimizer")

        cfg = st.session_state.get("config")
        if cfg is None:
            cfg = load_config()
            st.session_state.config = cfg

        active_engine = st.session_state.get("engine")
        active_info = st.session_state.get("connection_info", {})
        active_id = st.session_state.get("active_conn_id")
        selected_db = st.session_state.get("selected_database")
        connections = load_connections()

        # ── 1. Top-Left Connection Status & Quick Controls ───────────────────
        if active_engine:
            dtype = active_info.get("type", "db").upper()
            icon = _TYPE_ICONS.get(active_info.get("type", ""), "🔌")
            db_display = f" → `{selected_db}`" if selected_db else ""

            st.markdown(
                f"""
                <div style="background:rgba(34, 197, 94, 0.1); border:1px solid rgba(34, 197, 94, 0.3); border-radius:10px; padding:10px 14px; margin-bottom:10px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:700; color:#4ade80; font-size:0.85rem;">🟢 CONNECTED</span>
                        <span style="font-size:0.75rem; background:rgba(255,255,255,0.1); padding:2px 6px; border-radius:4px;">{icon} {dtype}</span>
                    </div>
                    <div style="font-weight:600; font-size:0.95rem; margin-top:4px; color:#f8fafc;">
                        {active_info.get('name', 'Active Connection')}{db_display}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col_disc, col_test = st.columns([1, 1])
            with col_disc:
                if st.button("⏏ Disconnect", use_container_width=True):
                    _disconnect()
                    st.rerun()
            with col_test:
                st.session_state.is_test_db = st.toggle(
                    "⚡ Execution",
                    value=st.session_state.get("is_test_db", False),
                    help="When ON: allows executing OPTIMIZE, VACUUM, ALTER, CREATE/DROP INDEX directly on the database. When OFF: safe read-only inspection mode.",
                )

        else:
            st.markdown(
                """
                <div style="background:rgba(148, 163, 184, 0.08); border:1px solid rgba(148, 163, 184, 0.2); border-radius:10px; padding:10px 14px; margin-bottom:10px;">
                    <div style="font-weight:600; color:#94a3b8; font-size:0.85rem;">⚪ NOT CONNECTED</div>
                    <div style="font-size:0.8rem; color:#64748b; margin-top:2px;">Select or create a connection below</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.divider()

        # ── 2. Quick Connection Launcher (Top Left) ──────────────────────────
        st.markdown("##### 🔌 **Quick Connect**")

        # Instant 1-click Demo Database Button
        is_demo_active = active_id == "demo-ecommerce-db"
        if st.button(
            "🚀 Load Demo Database" if not is_demo_active else "✅ Demo Database Active",
            use_container_width=True,
            type="primary" if not active_engine else "secondary",
            help="Instantly load sample e-commerce database with optimization opportunities",
        ):
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
                st.error(f"Failed to load demo DB: {exc}")

        # Init sidebar mode: "list" | "new" | "edit:<conn_id>"
        if "sidebar_mode" not in st.session_state:
            st.session_state.sidebar_mode = "list"

        # ── 3. Connection List & Management ──────────────────────────────────
        if st.session_state.sidebar_mode == "list":
            if connections:
                st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#94a3b8; margin:10px 0 6px 0;'>SAVED SERVERS</div>", unsafe_allow_html=True)
                for conn in connections:
                    cid = conn["id"]
                    is_active = cid == active_id
                    icon = _TYPE_ICONS.get(conn.get("type", ""), "🔌")
                    badge = conn.get("type", "").upper()

                    row = st.columns([5, 1])
                    btn_label = f"{'✓ ' if is_active else ''}{icon} {conn['name']}"
                    if row[0].button(
                        btn_label,
                        key=f"conn_btn_{cid}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                    ):
                        if not is_active:
                            pwd = get_password(cid) if conn.get("type") != "sqlite" else ""
                            with st.spinner(f"Connecting to {conn['name']}…"):
                                try:
                                    _do_connect(conn, pwd)
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Connection Failed: {exc}")

                    if row[1].button("✏️", key=f"edit_btn_{cid}", help="Edit connection parameters"):
                        st.session_state.sidebar_mode = f"edit:{cid}"
                        st.rerun()

            st.write("")
            if st.button("＋ Add New Connection", use_container_width=True):
                st.session_state.sidebar_mode = "new"
                st.rerun()

        # ── New connection form ───────────────────────────────────────────────
        elif st.session_state.sidebar_mode == "new":
            st.markdown("##### ＋ New Connection")
            if st.button("← Back to List", key="back_new", use_container_width=True):
                st.session_state.sidebar_mode = "list"
                st.rerun()

            save_btn, test_btn, _, conn, password = _connection_form(form_key="new")

            if test_btn:
                with st.spinner("Testing connection…"):
                    try:
                        build_engine(
                            conn["type"],
                            conn["host"],
                            conn["port"],
                            conn["user"],
                            password,
                            conn.get("database", ""),
                            conn.get("sqlite_path", ""),
                        )
                        st.success("Connection Successful! ✅")
                    except Exception as exc:
                        st.error(f"Connection Failed: {exc}")

            if save_btn:
                with st.spinner("Connecting and saving…"):
                    try:
                        _do_connect(conn, password)
                        saved = save_connection(conn, password)
                        st.session_state.config = load_config()
                        st.session_state.active_conn_id = saved["id"]
                        st.session_state.sidebar_mode = "list"
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Connection failed: {exc}")

        # ── Edit connection form ──────────────────────────────────────────────
        elif st.session_state.sidebar_mode.startswith("edit:"):
            edit_id = st.session_state.sidebar_mode.split(":", 1)[1]
            existing = next((c for c in connections if c["id"] == edit_id), None)

            if existing is None:
                st.session_state.sidebar_mode = "list"
                st.rerun()

            st.markdown(f"##### ✏️ Edit: {existing['name']}")
            if st.button("← Back to List", key="back_edit", use_container_width=True):
                st.session_state.sidebar_mode = "list"
                st.rerun()

            save_btn, test_btn, del_btn, conn, password = _connection_form(existing=existing, form_key=edit_id)
            conn["id"] = edit_id
            effective_pwd = password if password else (get_password(edit_id) if conn.get("type") != "sqlite" else "")

            if test_btn:
                with st.spinner("Testing connection…"):
                    try:
                        build_engine(
                            conn["type"],
                            conn["host"],
                            conn["port"],
                            conn["user"],
                            effective_pwd,
                            conn.get("database", ""),
                            conn.get("sqlite_path", ""),
                        )
                        st.success("Connection Successful! ✅")
                    except Exception as exc:
                        st.error(f"Connection Failed: {exc}")

            if save_btn:
                with st.spinner("Saving changes…"):
                    try:
                        _do_connect(conn, effective_pwd)
                        save_connection(conn, effective_pwd if password else "")
                        st.session_state.config = load_config()
                        st.session_state.active_conn_id = edit_id
                        st.session_state.sidebar_mode = "list"
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Connection failed: {exc}")

            if del_btn:
                if active_id == edit_id:
                    _disconnect()
                delete_connection(edit_id)
                st.session_state.config = load_config()
                st.session_state.sidebar_mode = "list"
                st.rerun()

        # ── 4. AI Engine & Local Ollama Status ────────────────────────────────
        st.divider()
        provider = cfg.get("ai", {}).get("provider", "ollama")
        ollama_url = cfg.get("ai", {}).get("ollama", {}).get("base_url", "http://localhost:11434")
        configured_model = cfg.get("ai", {}).get(provider, {}).get("model", "qwen2.5-coder:14b")

        if provider == "ollama":
            online, local_models = check_ollama_status(ollama_url)
            if online:
                st.markdown(
                    f"""
                    <div style="background:rgba(34, 197, 94, 0.08); border:1px solid rgba(34, 197, 94, 0.25); border-radius:8px; padding:6px 10px; margin-bottom:8px;">
                        <span style="color:#4ade80; font-weight:600; font-size:0.8rem;">🟢 OLLAMA ONLINE</span>
                        <div style="font-size:0.75rem; color:#94a3b8; margin-top:2px;">Local RTX Accelerated</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if local_models:
                    model_idx = local_models.index(configured_model) if configured_model in local_models else 0
                    sel_model = st.selectbox("Active Local Model", local_models, index=model_idx, key="sidebar_ollama_model")
                    if sel_model != configured_model:
                        cfg["ai"]["ollama"]["model"] = sel_model
                        st.session_state.config = cfg
                else:
                    st.caption(f"Model: `{configured_model}` (no other tags found)")
            else:
                st.markdown(
                    """
                    <div style="background:rgba(148, 163, 184, 0.08); border:1px solid rgba(148, 163, 184, 0.2); border-radius:8px; padding:6px 10px; margin-bottom:8px;">
                        <span style="color:#94a3b8; font-weight:600; font-size:0.8rem;">⚪ OLLAMA OFFLINE</span>
                        <div style="font-size:0.75rem; color:#64748b; margin-top:2px;">Run <code>ollama serve</code></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption(f"🤖 AI: **{provider.upper()}** (`{configured_model}`)")

        # ── 5. Shut Down / Quit Server Button ────────────────────────────────
        st.divider()
        with st.popover("🛑 Quit Server", use_container_width=True):
            st.markdown("**Quit & Stop SQL Helper**")
            st.caption("This will disconnect active database sessions and stop the local Python server process.")
            if st.button("Confirm & Stop Server", type="primary", use_container_width=True, key="side_quit_btn"):
                if st.session_state.get("engine"):
                    try:
                        st.session_state.engine.dispose()
                    except Exception:
                        pass
                st.success("Server stopped. You can close this browser window.")
                time.sleep(0.5)
                os._exit(0)
