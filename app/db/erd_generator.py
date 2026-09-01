"""
Entity-Relationship Diagram (ERD) & Schema Graph Generator.
Extracts schema topology (tables, columns, primary keys, foreign keys) and compiles
interactive Mermaid ERD and Graphviz DOT representations.
"""
from __future__ import annotations
import re
from typing import Any
from sqlalchemy import Engine
from app.db import schema_reader as sr


def get_schema_topology(
    engine: Engine,
    database: str = "",
    selected_tables: list[str] | None = None,
    include_referenced: bool = True,
) -> dict[str, Any]:
    """
    Extracts tables, columns, primary keys, and foreign keys for the target database.
    Optionally filters by selected_tables and automatically includes referenced foreign tables.
    """
    all_tables = sr.list_tables(engine, database)
    if not all_tables:
        return {"tables": {}, "relationships": []}

    target_tables = set(selected_tables if selected_tables else all_tables)

    # First pass: collect foreign keys to identify referenced tables
    raw_fks = {}
    for tbl in all_tables:
        if tbl in target_tables or include_referenced:
            try:
                fks = sr.get_foreign_keys(engine, database, tbl)
                raw_fks[tbl] = fks
                if include_referenced and tbl in target_tables:
                    for fk in fks:
                        ref_t = fk.get("referred_table")
                        if ref_t and ref_t in all_tables:
                            target_tables.add(ref_t)
            except Exception:
                raw_fks[tbl] = []

    # Second pass: build complete table topology
    tables_data: dict[str, dict] = {}
    relationships: list[dict] = []

    for tbl in sorted(target_tables):
        try:
            cols = sr.get_columns(engine, database, tbl)
            fks = raw_fks.get(tbl, [])
            if not fks and tbl not in raw_fks:
                fks = sr.get_foreign_keys(engine, database, tbl)

            pks = [c["column"] for c in cols if c.get("key") == "PRI" or c.get("extra") == "auto_increment"]

            tables_data[tbl] = {
                "name": tbl,
                "columns": cols,
                "primary_keys": pks,
                "foreign_keys": fks,
            }

            for fk in fks:
                ref_table = fk.get("referred_table")
                if ref_table and ref_table in target_tables:
                    constrained = fk.get("constrained_columns", [])
                    referred = fk.get("referred_columns", [])
                    c_col = constrained[0] if constrained else "id"
                    r_col = referred[0] if referred else "id"

                    relationships.append({
                        "from_table": tbl,
                        "from_col": c_col,
                        "to_table": ref_table,
                        "to_col": r_col,
                        "name": fk.get("name", f"fk_{tbl}_{ref_table}"),
                    })
        except Exception:
            continue

    return {
        "tables": tables_data,
        "relationships": relationships,
    }


def generate_mermaid_erd(
    engine: Engine,
    database: str = "",
    selected_tables: list[str] | None = None,
    include_referenced: bool = True,
    max_tables: int = 60,
) -> str:
    """
    Generates a Mermaid.js ER diagram string (erDiagram).
    """
    topology = get_schema_topology(engine, database, selected_tables, include_referenced)
    tables = topology["tables"]
    relationships = topology["relationships"]

    if not tables:
        return "erDiagram\n    EMPTY_SCHEMA {\n        string note \"No tables found\"\n    }"

    # If too many tables, cap to prevent UI lag
    if len(tables) > max_tables:
        top_table_keys = list(tables.keys())[:max_tables]
        tables = {k: tables[k] for k in top_table_keys}
        relationships = [
            r for r in relationships
            if r["from_table"] in tables and r["to_table"] in tables
        ]

    lines = ["erDiagram"]

    # 1. Output Table Entities
    for tbl_name, t_info in tables.items():
        clean_tbl = _clean_identifier(tbl_name)
        lines.append(f"    {clean_tbl} {{")
        for col in t_info["columns"][:18]:  # show up to 18 cols per table for clarity
            c_name = _clean_identifier(col["column"])
            c_type = _clean_type_for_mermaid(col.get("type", "string"))
            is_pk = "PK" if col["column"] in t_info["primary_keys"] else ""
            is_fk = "FK" if any(col["column"] in fk.get("constrained_columns", []) for fk in t_info["foreign_keys"]) else ""
            key_tag = is_pk or is_fk
            if key_tag:
                lines.append(f"        {c_type} {c_name} {key_tag}")
            else:
                lines.append(f"        {c_type} {c_name}")
        if len(t_info["columns"]) > 18:
            lines.append(f"        string more_columns__{len(t_info['columns']) - 18}...")
        lines.append("    }")

    # 2. Output Relationships (1-to-many: ||--o{ or }o--||)
    seen_rels = set()
    for rel in relationships:
        from_t = _clean_identifier(rel["from_table"])
        to_t = _clean_identifier(rel["to_table"])
        rel_key = f"{from_t}->{to_t}:{rel['from_col']}"
        if rel_key in seen_rels:
            continue
        seen_rels.add(rel_key)

        label = f'"{rel["from_col"]} -> {rel["to_col"]}"'
        lines.append(f"    {to_t} ||--o{{ {from_t} : {label}")

    return "\n".join(lines)


def generate_graphviz_erd(
    engine: Engine,
    database: str = "",
    selected_tables: list[str] | None = None,
    include_referenced: bool = True,
    max_tables: int = 50,
) -> str:
    """
    Generates a Graphviz DOT representation with HTML-like record tables.
    """
    topology = get_schema_topology(engine, database, selected_tables, include_referenced)
    tables = topology["tables"]
    relationships = topology["relationships"]

    if not tables:
        return 'digraph ERD { node [shape=box]; "No tables found"; }'

    if len(tables) > max_tables:
        top_table_keys = list(tables.keys())[:max_tables]
        tables = {k: tables[k] for k in top_table_keys}
        relationships = [
            r for r in relationships
            if r["from_table"] in tables and r["to_table"] in tables
        ]

    lines = [
        "digraph ERD {",
        "    graph [rankdir=LR, bgcolor=transparent, pad=0.5, nodesep=0.6, ranksep=1.0];",
        "    node [shape=none, fontname=\"Inter, Helvetica, Arial, sans-serif\", fontsize=11];",
        "    edge [fontname=\"Inter, Helvetica, Arial, sans-serif\", fontsize=9, color=\"#6366f1\", fontcolor=\"#94a3b8\", penwidth=1.5];",
        "",
    ]

    for tbl_name, t_info in tables.items():
        clean_tbl = _clean_identifier(tbl_name)
        rows_html = [
            f'<<table border="0" cellborder="1" cellspacing="0" cellpadding="5" bgcolor="#1e293b" style="rounded" color="#334155">',
            f'<tr><td colspan="2" bgcolor="#3b82f6" align="center"><font color="#ffffff"><b>{tbl_name}</b></font></td></tr>',
        ]

        for col in t_info["columns"][:16]:
            c_name = col["column"]
            c_type = col.get("type", "").upper()
            is_pk = col["column"] in t_info["primary_keys"]
            is_fk = any(col["column"] in fk.get("constrained_columns", []) for fk in t_info["foreign_keys"])
            
            icon = "🔑 " if is_pk else ("🔗 " if is_fk else "")
            font_color = "#38bdf8" if is_pk else ("#a78bfa" if is_fk else "#e2e8f0")
            
            rows_html.append(
                f'<tr><td align="left" port="{c_name}"><font color="{font_color}">{icon}{c_name}</font></td>'
                f'<td align="right"><font color="#94a3b8">{c_type}</font></td></tr>'
            )

        if len(t_info["columns"]) > 16:
            rows_html.append(f'<tr><td colspan="2" align="center"><font color="#64748b"><i>... +{len(t_info["columns"]) - 16} more columns</i></font></td></tr>')

        rows_html.append("</table>>")
        lines.append(f'    "{clean_tbl}" [label={"".join(rows_html)}];')

    lines.append("")

    for rel in relationships:
        from_t = _clean_identifier(rel["from_table"])
        to_t = _clean_identifier(rel["to_table"])
        from_col = rel["from_col"]
        to_col = rel["to_col"]
        lines.append(f'    "{from_t}":{from_col} -> "{to_t}":{to_col} [label=" {from_col} "];')

    lines.append("}")
    return "\n".join(lines)


def _clean_identifier(name: str) -> str:
    """Sanitizes table and column names for Mermaid syntax."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if cleaned and cleaned[0].isdigit():
        cleaned = "t_" + cleaned
    return cleaned or "tbl"


def _clean_type_for_mermaid(type_str: str) -> str:
    """Converts SQL data types into clean Mermaid scalar types."""
    t = type_str.upper()
    if "INT" in t:
        return "int"
    if "CHAR" in t or "TEXT" in t or "VARCHAR" in t or "CLOB" in t:
        return "string"
    if "DECIMAL" in t or "NUMERIC" in t or "FLOAT" in t or "DOUBLE" in t or "REAL" in t:
        return "float"
    if "DATE" in t or "TIME" in t:
        return "datetime"
    if "BOOL" in t:
        return "boolean"
    if "BLOB" in t or "BYTE" in t or "BINARY" in t:
        return "blob"
    return "string"
