from __future__ import annotations
import re
from app.ai.index_advisor import _create_index_sql

SENSITIVE_PATTERNS = re.compile(
    r"(password|passwd|pwd|secret|token|api[_]?key|ssn|credit[_]?card|"
    r"cvv|social[_]?security|private[_]?key|auth[_]?token|access[_]?key|"
    r"encryption[_]?key|salt)",
    re.IGNORECASE,
)

DYNAMIC_SQL_PATTERNS = re.compile(
    r"(EXEC\s*\(|EXECUTE\s*\(|sp_executesql|PREPARE\s+|EXECUTE\s+stmt|"
    r"CONCAT\s*\(.*SELECT|SET\s+@sql)",
    re.IGNORECASE,
)

BROAD_PRIVILEGE_PATTERNS = re.compile(
    r"GRANT\s+ALL|GRANT\s+.*\*\.\*|WITH\s+GRANT\s+OPTION",
    re.IGNORECASE,
)


def run_static_checks(
    tables: list[str],
    columns_by_table: dict[str, list[dict]],
    indexes_by_table: dict[str, list[dict]],
    fk_by_table: dict[str, list[dict]],
    procedure_bodies: dict[str, str],
    grants: list[str],
    dialect: str = "sqlite",
    database: str = "",
) -> list[dict]:
    findings: list[dict] = []

    # 1. Tables without primary keys
    for tbl, cols in columns_by_table.items():
        has_pk = any(c["key"] == "PRI" for c in cols)
        if not has_pk:
            first_id_col = next((c["column"] for c in cols if "id" in c["column"].lower()), None)
            fix_sql = ""
            if first_id_col:
                if dialect == "mysql":
                    tbl_q = f"`{database}`.`{tbl}`" if database else f"`{tbl}`"
                    fix_sql = f"ALTER TABLE {tbl_q} ADD PRIMARY KEY (`{first_id_col}`);"
                elif dialect == "postgresql":
                    fix_sql = f'ALTER TABLE "{tbl}" ADD PRIMARY KEY ("{first_id_col}");'

            findings.append({
                "severity": "Critical",
                "category": "Schema Integrity",
                "table": tbl,
                "message": f"Table `{tbl}` has no PRIMARY KEY. This prevents deterministic row lookups and replication.",
                "action_sql": fix_sql,
                "fix_label": f"Add PK on `{first_id_col}`" if first_id_col else "",
            })

    # 2. Sensitive plain-text columns
    for tbl, cols in columns_by_table.items():
        for col in cols:
            if SENSITIVE_PATTERNS.search(col["column"]):
                col_type = col.get("type", "").lower()
                if not any(t in col_type for t in ("varbinary", "blob", "binary")):
                    findings.append({
                        "severity": "Critical",
                        "category": "Data Protection",
                        "table": tbl,
                        "message": (
                            f"`{tbl}`.`{col['column']}` ({col['type']}) appears to store sensitive data in plaintext. "
                            "Consider applying one-way hashing (bcrypt/Argon2) or symmetric encryption at application layer."
                        ),
                        "action_sql": "",
                        "fix_label": "",
                    })

    # 3. FK columns without indexes
    for tbl, fks in fk_by_table.items():
        indexed_cols = {
            col
            for idx in indexes_by_table.get(tbl, [])
            for col in idx.get("columns", [])
        }
        for fk in fks:
            if fk["column"] not in indexed_cols:
                idx_name = f"idx_{tbl}_{fk['column']}"
                fix_sql = _create_index_sql(idx_name, tbl, [fk["column"]], dialect, database)
                findings.append({
                    "severity": "Warning",
                    "category": "Performance & Integrity",
                    "table": tbl,
                    "message": (
                        f"`{tbl}`.`{fk['column']}` is a FK referencing `{fk['ref_table']}({fk['ref_column']})` "
                        "but has no leading index. Causes full scans on cascade deletes and joins."
                    ),
                    "action_sql": fix_sql,
                    "fix_label": f"Create Index `{idx_name}`",
                })

    # 4. Dynamic SQL in procedures
    for name, body in procedure_bodies.items():
        if DYNAMIC_SQL_PATTERNS.search(body):
            findings.append({
                "severity": "Critical",
                "category": "SQL Injection Vector",
                "table": name,
                "message": (
                    f"Stored procedure `{name}` constructs dynamic SQL queries. "
                    "Verify that all dynamic inputs are properly bound and parameterized."
                ),
                "action_sql": "",
                "fix_label": "",
            })

    # 5. Overly broad MySQL grants
    for grant in grants:
        if BROAD_PRIVILEGE_PATTERNS.search(grant):
            findings.append({
                "severity": "Warning",
                "category": "Privilege Hardening",
                "table": "Global",
                "message": f"Broad privilege detected: `{grant.strip()}`. Review user grants to apply least privilege.",
                "action_sql": "",
                "fix_label": "",
            })

    return findings


def build_security_prompt(db_type: str, schema_summary: str, static_findings: list[dict]) -> str:
    findings_text = "\n".join(
        f"- [{f['severity']}] {f['category']} | {f['table']}: {f['message']}"
        for f in static_findings
    ) or "None detected by static analysis."

    return f"""You are a senior database security engineer and compliance auditor.
Analyze the following {db_type.upper()} schema and static findings.

Schema summary:
{schema_summary}

Static analysis already found these issues:
{findings_text}

Provide a structured security review:
1. ADDITIONAL OBSERVATIONS
Unidentified schema security or data integrity issues not flagged above.

2. ENCRYPTION & DATA PROTECTION
Specific column-level encryption or tokenization strategies.

3. PRIVILEGE HARDENING
Role-based access control and principle of least privilege recommendations.

4. AUDIT & LOGGING
Recommendations for CDC (Change Data Capture) or audit triggers.

5. SECURITY SCORE
Give an overall security grade (1 to 10) with justification.
"""
