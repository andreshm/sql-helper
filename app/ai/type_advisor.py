"""
AI Semantic Data Type Auditor & Risk Advisor.
Audits proposed data type migrations against real-world business domain conventions,
external API standards, international formatting, and future growth requirements.
"""
from __future__ import annotations
import re
from typing import Any
from app.ai.provider import ask


def build_type_audit_prompt(
    db_type: str,
    table: str,
    suggestions: list[dict],
    sample_rows: list[dict] | None = None,
) -> str:
    """
    Builds a specialized prompt for auditing proposed data type changes against domain semantics for a single table.
    """
    sugg_lines = []
    for s in suggestions:
        col = s["column"]
        curr = s["current_type"]
        sugg = s["suggested_type"]
        cat = s["category"]
        reason = s["reason"]
        sugg_lines.append(f"- Column `{col}`: `{curr}` -> `{sugg}` [{cat}]\n  Deterministic Finding: {reason}")

    proposals_text = "\n".join(sugg_lines) if sugg_lines else "(No migrations proposed)"

    sample_text = ""
    if sample_rows:
        sample_text = "\nSample Row Context:\n" + "\n".join(
            f"  {r}" for r in sample_rows[:3]
        ) + "\n"

    return f"""You are a Principal Database Architect and Data Domain Specialist.
Your task is to audit the proposed database column type migrations for table `{table}` ({db_type.upper()}) to prevent future application bugs and data truncation.

BACKGROUND & RESPONSIBILITY:
Deterministic SQL profiling found that existing data values fit into smaller types.
However, you must evaluate the SEMANTIC MEANING and FUTURE BUSINESS RULES of each column:
1. IDENTIFIER INTEGRITY: Do not convert columns that represent external tracking numbers (UPS, FedEx), barcodes, SKU codes, VINs, or international postal codes to integer types, even if current rows happen to only contain digits.
2. STANDARDS & FIXED LENGTHS: Identify ISO codes (e.g. ISO-3166-1 alpha-2 country codes should be fixed CHAR(2)), UUIDs (CHAR(36) or UUID), or currency fields (DECIMAL(12, 2)).
3. FUTURE EXTENSIBILITY: Does shrinking a VARCHAR too tightly (e.g. down to 30 chars for names or addresses) risk future user input truncation?

PROPOSED MIGRATIONS TO AUDIT:
{proposals_text}
{sample_text}
INSTRUCTIONS:
Respond with a clear, concise audit for each proposed column migration in this exact format:

For each column:
COLUMN: [column_name]
STATUS: [APPROVED | CAUTION | ALTERNATIVE]
CONFIDENCE: [HIGH | MEDIUM | LOW]
ANALYSIS: 1-2 sentences explaining why this migration is safe or what future business risk exists (e.g. external carrier formats, international chars, growth ceiling).

SUMMARY VERDICT:
One concluding sentence on overall migration safety.
"""


def parse_ai_type_audit(raw_response: str) -> dict[str, dict]:
    """
    Parses the structured single-table AI audit response into a dictionary keyed by column name.
    """
    results: dict[str, dict] = {}
    blocks = re.split(r"COLUMN:\s*", raw_response, flags=re.IGNORECASE)

    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        col_name = lines[0].strip("`*\"' ")
        
        status = "APPROVED"
        confidence = "HIGH"
        analysis = ""

        for line in lines[1:]:
            l_upper = line.upper()
            if l_upper.startswith("STATUS:"):
                st = line.split(":", 1)[1].strip().upper()
                if "CAUTION" in st: status = "CAUTION"
                elif "ALTERNATIVE" in st: status = "ALTERNATIVE"
                else: status = "APPROVED"
            elif l_upper.startswith("CONFIDENCE:"):
                conf = line.split(":", 1)[1].strip().upper()
                if "LOW" in conf: confidence = "LOW"
                elif "MEDIUM" in conf: confidence = "MEDIUM"
                else: confidence = "HIGH"
            elif l_upper.startswith("ANALYSIS:"):
                analysis = line.split(":", 1)[1].strip()

        if col_name and not col_name.startswith("SUMMARY"):
            results[col_name.lower()] = {
                "status": status,
                "confidence": confidence,
                "analysis": analysis,
            }

    return results


def build_database_wide_type_audit_prompt(
    db_type: str,
    all_suggestions: dict[str, list[dict]],
) -> str:
    """
    Builds a consolidated database-wide audit prompt evaluating all proposed column migrations across all tables.
    """
    table_blocks = []
    for tbl, suggs in all_suggestions.items():
        s_lines = [f"TABLE: `{tbl}`"]
        for s in suggs:
            col = s["column"]
            curr = s["current_type"]
            sugg = s["suggested_type"]
            cat = s["category"]
            reason = s["reason"]
            s_lines.append(f"  - Column `{col}`: `{curr}` -> `{sugg}` [{cat}] | {reason}")
        table_blocks.append("\n".join(s_lines))

    proposals_text = "\n\n".join(table_blocks) if table_blocks else "(No migrations proposed)"

    return f"""You are a Principal Database Architect and Data Domain Specialist.
Your task is to audit proposed column data type migrations across an entire `{db_type.upper()}` database.

OBJECTIVE:
Deterministic SQL profiling verified that current table rows fit into smaller types.
Review each proposed column migration against business domain conventions:
1. IDENTIFIER INTEGRITY: Flag carrier tracking numbers, barcodes, SKUs, serial numbers, and postal codes that must stay strings.
2. STANDARDS: Confirm ISO country codes (CHAR(2)), UUIDs (CHAR(36)), and currencies (DECIMAL(12, 2)).
3. FUTURE EXTENSIBILITY: Warn if shrinking a VARCHAR is too aggressive for future expansion.

ALL PROPOSED MIGRATIONS:
{proposals_text}

INSTRUCTIONS:
For every column, output strictly in this format:

TABLE: [table_name]
COLUMN: [column_name]
STATUS: [APPROVED | CAUTION | ALTERNATIVE]
CONFIDENCE: [HIGH | MEDIUM | LOW]
ANALYSIS: 1 sentence explaining safety or risk.

SUMMARY:
Brief concluding evaluation of overall schema safety.
"""


def parse_database_wide_ai_type_audit(raw_response: str) -> dict[str, dict[str, dict]]:
    """
    Parses a database-wide audit into a nested dictionary: results[table_name][column_name] = {...}
    """
    results: dict[str, dict[str, dict]] = {}
    current_tbl = ""
    curr_col = ""
    curr_status = "APPROVED"
    curr_conf = "HIGH"
    curr_analysis = ""

    def _save():
        nonlocal current_tbl, curr_col, curr_status, curr_conf, curr_analysis
        if current_tbl and curr_col:
            tbl_key = current_tbl.lower().strip("`*\"' ")
            if tbl_key not in results:
                results[tbl_key] = {}
            results[tbl_key][curr_col.lower().strip("`*\"' ")] = {
                "status": curr_status,
                "confidence": curr_conf,
                "analysis": curr_analysis,
            }

    for line in raw_response.splitlines():
        line_s = line.strip()
        l_upper = line_s.upper()

        if l_upper.startswith("TABLE:"):
            _save()
            current_tbl = line_s.split(":", 1)[1].strip("`*\"' ")
            curr_col = ""
            curr_status = "APPROVED"
            curr_conf = "HIGH"
            curr_analysis = ""
        elif l_upper.startswith("COLUMN:"):
            _save()
            curr_col = line_s.split(":", 1)[1].strip("`*\"' ")
            curr_status = "APPROVED"
            curr_conf = "HIGH"
            curr_analysis = ""
        elif l_upper.startswith("STATUS:"):
            st = line_s.split(":", 1)[1].strip().upper()
            if "CAUTION" in st: curr_status = "CAUTION"
            elif "ALTERNATIVE" in st: curr_status = "ALTERNATIVE"
            else: curr_status = "APPROVED"
        elif l_upper.startswith("CONFIDENCE:"):
            cf = line_s.split(":", 1)[1].strip().upper()
            if "LOW" in cf: curr_conf = "LOW"
            elif "MEDIUM" in cf: curr_conf = "MEDIUM"
            else: curr_conf = "HIGH"
        elif l_upper.startswith("ANALYSIS:"):
            curr_analysis = line_s.split(":", 1)[1].strip()

    _save()
    return results
