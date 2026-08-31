from __future__ import annotations


def generate_insert_template(db_type: str, database: str, table: str, columns: list[dict]) -> str:
    non_auto = [c for c in columns if "auto_increment" not in c.get("extra", "").lower() and c["key"] != "PRI"]
    if not non_auto:
        non_auto = columns

    col_names = ", ".join(f"`{c['column']}`" if db_type == "mysql" else f'"{c["column"]}"' for c in non_auto)
    placeholders = ", ".join(f":{c['column']}" for c in non_auto)
    comments = "\n".join(
        f"-- {c['column']}: {c['type']}{' NOT NULL' if not c['nullable'] else ''}"
        f"{' DEFAULT ' + str(c['default']) if c['default'] is not None else ''}"
        for c in non_auto
    )

    if db_type == "mysql":
        tbl = f"`{database}`.`{table}`"
    else:
        tbl = f'"{table}"'

    return f"-- Column reference:\n{comments}\n\nINSERT INTO {tbl} ({col_names})\nVALUES ({placeholders});"


def generate_update_template(db_type: str, database: str, table: str, columns: list[dict]) -> str:
    pk_cols = [c for c in columns if c["key"] == "PRI"]
    non_pk = [c for c in columns if c["key"] != "PRI" and "auto_increment" not in c.get("extra", "").lower()]
    if not non_pk:
        non_pk = columns

    if db_type == "mysql":
        tbl = f"`{database}`.`{table}`"
        q = "`"
    else:
        tbl = f'"{table}"'
        q = '"'

    set_clause = ",\n    ".join(f"{q}{c['column']}{q} = :{c['column']}" for c in non_pk)
    where_clause = " AND ".join(
        f"{q}{c['column']}{q} = :{c['column']}_pk" for c in pk_cols
    ) or "1=1  -- add your WHERE condition"

    return f"UPDATE {tbl}\nSET\n    {set_clause}\nWHERE {where_clause};"


def generate_delete_template(db_type: str, database: str, table: str, columns: list[dict]) -> str:
    pk_cols = [c for c in columns if c["key"] == "PRI"]
    q = "`" if db_type == "mysql" else '"'
    tbl = f"`{database}`.`{table}`" if db_type == "mysql" else f'"{table}"'
    where_clause = " AND ".join(
        f"{q}{c['column']}{q} = :{c['column']}" for c in pk_cols
    ) or "1=1  -- DANGER: add a WHERE clause!"

    return f"-- WARNING: This will delete rows. Review carefully.\nDELETE FROM {tbl}\nWHERE {where_clause};"


def build_query_review_prompt(db_type: str, sql: str, schema_summary: str) -> str:
    return f"""You are a senior database engineer. Review the following {db_type.upper()} SQL query for:
1. Correctness (syntax, logic)
2. Performance issues (missing indexes, full scans, N+1, etc.)
3. Safety (SQL injection risk if values are hardcoded, missing WHERE clause on DELETE/UPDATE)
4. Suggestions for improvement

Schema context:
{schema_summary}

Query to review:
```sql
{sql}
```

Provide a concise, structured review with a verdict: Safe / Needs Review / Dangerous.
"""
