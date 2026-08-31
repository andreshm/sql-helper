from __future__ import annotations


def build_nl_to_sql_prompt(
    db_type: str,
    database: str,
    question: str,
    schema_context: str,
    focus_tables: list[str],
    hints: list[str],
) -> str:
    table_note = (
        f"Focus primarily on these tables: {', '.join(focus_tables)}."
        if focus_tables
        else "Use whatever tables from the schema are relevant."
    )

    hint_text = ""
    if hints:
        hint_text = "\nOptimization preferences:\n" + "\n".join(f"- {h}" for h in hints)

    return f"""You are an expert {db_type.upper()} database engineer.
Your task is to convert a plain-English question into an optimized {db_type.upper()} query.

DATABASE: {database}

SCHEMA:
{schema_context}

{table_note}
{hint_text}

QUESTION: {question}

Respond in this exact format — no extra prose outside the sections:

UNDERSTANDING
One sentence: what the query needs to return.

SQL
```sql
<the complete optimized query here>
```

EXPLANATION
- What each JOIN, subquery, or CTE does and why it was chosen.
- Which indexes this query will use (based on the schema above).
- Any performance notes or caveats.

ALTERNATIVES
If there is a simpler version (less optimal but easier to read) OR a more complex version \
(better performance at scale), show it briefly. Otherwise write "None."
"""


def build_explain_prompt(db_type: str, sql: str, explain_output: str) -> str:
    return f"""You are a {db_type.upper()} query performance expert.
Interpret the following EXPLAIN output and give a concise, actionable analysis.

SQL:
```sql
{sql}
```

EXPLAIN output:
```
{explain_output}
```

Respond with:
1. A plain-English summary of the execution plan.
2. Any full-table scans or expensive operations, and why they are happening.
3. Specific index or query rewrites that would improve performance.
Keep it under 200 words.
"""
