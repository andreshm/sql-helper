import streamlit as st
from app.ui.theme import apply_theme, render_metric_card
from app.ui.components.connection_form import render_connection_sidebar
from app.db import schema_reader as sr
from app.ai.provider import ask

st.set_page_config(page_title="AI Chat — SQL Helper", page_icon="🤖", layout="wide")
apply_theme()
render_connection_sidebar()

st.title("🤖 Database Assistant Chat")
st.caption("Ask questions, request schema refactoring advice, or get query help with full database schema context.")

engine = st.session_state.get("engine")
if engine is None:
    st.info("Connect to a database using the sidebar to chat with the AI assistant.")
    st.stop()

selected_db = st.session_state.get("selected_database", "")
dialect = engine.dialect.name
cfg = st.session_state.get("config", {})

# Build schema context
schema_key = f"schema_context_{selected_db}"
if schema_key not in st.session_state:
    with st.spinner("Loading database schema context…"):
        try:
            tables = sr.list_tables(engine, selected_db)
            lines = [f"Database Engine: {dialect.upper()} (Database: {selected_db})"]
            for tbl in tables[:40]:
                try:
                    cols = sr.get_columns(engine, selected_db, tbl)
                    col_str = ", ".join(f"{c['column']} {c['type']}{'*' if c.get('key')=='PRI' else ''}" for c in cols)
                    lines.append(f"  Table `{tbl}`: {col_str}")
                except Exception:
                    lines.append(f"  Table `{tbl}`")
            st.session_state[schema_key] = "\n".join(lines)
        except Exception as e:
            st.session_state[schema_key] = f"Schema unavailable: {e}"

schema_context = st.session_state[schema_key]

with st.expander("ℹ️ Schema Context Given to AI", expanded=False):
    st.text(schema_context)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# Quick Prompts
st.markdown("##### 💡 Suggested Questions")
suggestions = [
    "What are the top 3 indexing improvements for this schema?",
    "Suggest data type optimizations to save disk storage",
    "How can I reduce database size and bloat?",
    "Generate a sample analytical report query joining tables",
]
chips = st.columns(len(suggestions))
for idx, s in enumerate(suggestions):
    if chips[idx].button(s[:32] + "…", key=f"chip_{idx}", use_container_width=True):
        st.session_state.pending_chat = s

# Display conversation history
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Ask anything about your database, queries, or performance…")

if "pending_chat" in st.session_state and st.session_state.pending_chat:
    user_input = st.session_state.pending_chat
    st.session_state.pending_chat = None

if user_input:
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    sys_prompt = f"""You are a principal {dialect.upper()} database architect and query optimization expert.
You have full access to the following database schema:

{schema_context}

Provide concise, highly accurate, and practical advice. If generating SQL, ensure syntax is 100% valid for {dialect.upper()}.
"""
    full_prompt = f"{sys_prompt}\n\nUser Question: {user_input}"

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            response = ask(cfg, full_prompt)
        st.markdown(response)

    st.session_state.chat_history.append({"role": "assistant", "content": response})

if st.session_state.chat_history:
    if st.button("🗑️ Clear Chat History"):
        st.session_state.chat_history = []
        st.rerun()
