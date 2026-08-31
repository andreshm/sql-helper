"""
Connection persistence.
  - Metadata (host, port, user, db type, name, last_session) -> config.yaml
  - Passwords -> Windows Credential Manager via keyring (never written to disk)
"""
from __future__ import annotations
import re
import uuid
from pathlib import Path
from typing import Any

import keyring
import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"
_KEYRING_SERVICE = "sql_helper"


# ── Password helpers (keyring / Windows Credential Manager) ──────────────────

def save_password(conn_id: str, password: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, conn_id, password)


def get_password(conn_id: str) -> str:
    return keyring.get_password(_KEYRING_SERVICE, conn_id) or ""


def delete_password(conn_id: str) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, conn_id)
    except Exception:
        pass


# ── Config.yaml helpers ───────────────────────────────────────────────────────

def _read_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        with open(_CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    try:
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass


# ── Connection CRUD ───────────────────────────────────────────────────────────

def load_connections() -> list[dict]:
    """Return saved connections (no passwords)."""
    cfg = _read_config()
    return cfg.get("connections", [])


def save_connection(conn: dict, password: str = "") -> dict:
    """
    Persist a connection. Assigns an id if new.
    Password goes to keyring; everything else to config.yaml.
    Returns the saved connection dict (without password).
    """
    cfg = _read_config()
    connections: list[dict] = cfg.get("connections", [])

    if not conn.get("id"):
        conn["id"] = _unique_id(conn.get("name", "conn"), connections)

    # Strip password from the dict we store on disk
    safe = {k: v for k, v in conn.items() if k != "password"}

    # Update existing or append
    idx = next((i for i, c in enumerate(connections) if c["id"] == safe["id"]), None)
    if idx is not None:
        connections[idx] = safe
    else:
        connections.append(safe)

    cfg["connections"] = connections
    _write_config(cfg)

    if password:
        save_password(safe["id"], password)

    return safe


def delete_connection(conn_id: str) -> None:
    cfg = _read_config()
    cfg["connections"] = [c for c in cfg.get("connections", []) if c["id"] != conn_id]
    
    # If this was the last session connection, clear last_session
    last_sess = cfg.get("last_session", {})
    if last_sess.get("connection_id") == conn_id:
        cfg.pop("last_session", None)

    _write_config(cfg)
    delete_password(conn_id)


def _unique_id(name: str, existing: list[dict]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "conn"
    existing_ids = {c["id"] for c in existing}
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# ── Session State Persistence (Pick up where you left off) ───────────────────

def save_last_session(conn_id: str, database: str = "", tables: list[str] | None = None) -> None:
    """Save the last active connection, database, and selected tables."""
    cfg = _read_config()
    cfg["last_session"] = {
        "connection_id": conn_id,
        "database": database,
        "tables": tables or [],
    }
    _write_config(cfg)


def get_last_session() -> dict:
    """Retrieve last session parameters."""
    cfg = _read_config()
    return cfg.get("last_session", {})


def get_connection_by_id(conn_id: str) -> dict | None:
    """Find saved connection by id."""
    connections = load_connections()
    return next((c for c in connections if c["id"] == conn_id), None)


def persist_active_database(database: str) -> None:
    """Save the active database to last_session using session state connection id."""
    try:
        import streamlit as st
        if hasattr(st, "runtime") and st.runtime.exists() and hasattr(st, "session_state"):
            conn_id = st.session_state.get("active_conn_id") or st.session_state.get("connection_info", {}).get("id", "")
            tables = st.session_state.get("selected_tables", [])
            if conn_id and database:
                save_last_session(conn_id, database, tables)
    except Exception:
        pass
