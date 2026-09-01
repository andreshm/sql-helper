"""
Slow Query Log & Performance Schema Ingestion Analyzer.
Ingests live server query performance metrics (MySQL sys / performance_schema, Postgres pg_stat_statements)
and parses uploaded slow query log files to recommend traffic-driven composite indexes.
"""
from __future__ import annotations
import re
from typing import Any
from sqlalchemy import text, Engine


def fetch_live_slow_queries(engine: Engine, database: str = "", limit: int = 25) -> list[dict]:
    """
    Fetches real-time slow queries and full table scan digests directly from the server engine.
    """
    dialect = engine.dialect.name
    results = []

    with engine.connect() as conn:
        if dialect in ("mysql", "mariadb"):
            # 1. Try sys.statements_with_full_table_scans or sys.statement_analysis
            try:
                q = text(
                    "SELECT query, db, exec_count, total_latency, "
                    "no_index_used_count, no_good_index_used_count, "
                    "rows_sent_avg, rows_examined_avg, first_seen, last_seen "
                    "FROM sys.statements_with_full_table_scans "
                    "WHERE db = :db OR :db = '' "
                    "ORDER BY total_latency DESC LIMIT :limit"
                )
                rows = conn.execute(q, {"db": database, "limit": limit}).fetchall()
                for r in rows:
                    q_text = str(r[0]) if r[0] else ""
                    if not q_text or "sys." in q_text or "information_schema" in q_text:
                        continue
                    results.append({
                        "query": q_text,
                        "database": r[1] or database,
                        "exec_count": int(r[2] or 1),
                        "total_latency": str(r[3]),
                        "full_table_scan": True if (r[4] or 0) > 0 else False,
                        "rows_sent_avg": float(r[6] or 0),
                        "rows_examined_avg": float(r[7] or 0),
                        "inefficiency_ratio": round(float(r[7] or 0) / max(1.0, float(r[6] or 1)), 1),
                        "source": "sys.statements_with_full_table_scans",
                    })
            except Exception:
                # Fallback to performance_schema.events_statements_summary_by_digest
                try:
                    q = text(
                        "SELECT DIGEST_TEXT, SCHEMA_NAME, COUNT_STAR, "
                        "SUM_TIMER_WAIT / 1000000000000 as total_latency_sec, "
                        "SUM_NO_INDEX_USED, SUM_ROWS_SENT / COUNT_STAR as avg_rows_sent, "
                        "SUM_ROWS_EXAMINED / COUNT_STAR as avg_rows_examined "
                        "FROM performance_schema.events_statements_summary_by_digest "
                        "WHERE SCHEMA_NAME = :db OR :db = '' "
                        "ORDER BY SUM_TIMER_WAIT DESC LIMIT :limit"
                    )
                    rows = conn.execute(q, {"db": database, "limit": limit}).fetchall()
                    for r in rows:
                        q_text = str(r[0]) if r[0] else ""
                        if not q_text or "performance_schema" in q_text:
                            continue
                        results.append({
                            "query": q_text,
                            "database": r[1] or database,
                            "exec_count": int(r[2] or 1),
                            "total_latency": f"{round(float(r[3] or 0), 2)}s",
                            "full_table_scan": True if (r[4] or 0) > 0 else False,
                            "rows_sent_avg": float(r[5] or 0),
                            "rows_examined_avg": float(r[6] or 0),
                            "inefficiency_ratio": round(float(r[6] or 0) / max(1.0, float(r[5] or 1)), 1),
                            "source": "performance_schema",
                        })
                except Exception:
                    pass

        elif dialect == "postgresql":
            try:
                q = text(
                    "SELECT query, calls, total_exec_time / 1000.0 as total_latency_sec, "
                    "rows / calls as avg_rows "
                    "FROM pg_stat_statements "
                    "ORDER BY total_exec_time DESC LIMIT :limit"
                )
                rows = conn.execute(q, {"limit": limit}).fetchall()
                for r in rows:
                    results.append({
                        "query": str(r[0]),
                        "database": database,
                        "exec_count": int(r[1] or 1),
                        "total_latency": f"{round(float(r[2] or 0), 2)}s",
                        "full_table_scan": False,
                        "rows_sent_avg": float(r[3] or 0),
                        "rows_examined_avg": float(r[3] or 0) * 10,
                        "inefficiency_ratio": 10.0,
                        "source": "pg_stat_statements",
                    })
            except Exception:
                pass

    return results


def parse_uploaded_slow_query_log(raw_log_text: str) -> list[dict]:
    """
    Parses standard MySQL slow query log text files.
    """
    queries = []
    blocks = re.split(r"# Time:\s*", raw_log_text)

    for block in blocks:
        if not block.strip():
            continue

        q_time_m = re.search(r"Query_time:\s*([\d\.]+)", block)
        lock_time_m = re.search(r"Lock_time:\s*([\d\.]+)", block)
        rows_sent_m = re.search(r"Rows_sent:\s*(\d+)", block)
        rows_exam_m = re.search(r"Rows_examined:\s*(\d+)", block)

        q_time = float(q_time_m.group(1)) if q_time_m else 0.0
        rows_sent = int(rows_sent_m.group(1)) if rows_sent_m else 0
        rows_exam = int(rows_exam_m.group(1)) if rows_exam_m else 0

        # Extract SQL statements (lines after header comments)
        sql_lines = [l for l in block.splitlines() if not l.startswith("#") and not l.startswith("SET timestamp") and l.strip()]
        sql_text = " ".join(sql_lines).strip()

        if sql_text and ("SELECT" in sql_text.upper() or "UPDATE" in sql_text.upper() or "DELETE" in sql_text.upper()):
            queries.append({
                "query": sql_text,
                "database": "",
                "exec_count": 1,
                "total_latency": f"{q_time:.2f}s",
                "full_table_scan": rows_exam > 1000 and rows_sent < 100,
                "rows_sent_avg": float(rows_sent),
                "rows_examined_avg": float(rows_exam),
                "inefficiency_ratio": round(float(rows_exam) / max(1.0, float(rows_sent)), 1),
                "source": "Uploaded slow_query.log",
            })

    return queries


def recommend_indexes_for_slow_queries(
    slow_queries: list[dict],
    dialect: str = "mysql",
    existing_tables: list[str] | None = None,
) -> list[dict]:
    """
    Analyzes slow query patterns and generates traffic-tailored composite index recommendations.
    """
    recommendations = []
    seen_recommendations = set()

    for item in slow_queries:
        sql = item["query"]
        sql_upper = sql.upper()

        # 1. Identify Table
        from_match = re.search(r"\bFROM\s+[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql, re.IGNORECASE)
        table_name = from_match.group(1) if from_match else ""

        if not table_name:
            continue
        if existing_tables and table_name not in existing_tables:
            # Check lowercase match
            matched = next((t for t in existing_tables if t.lower() == table_name.lower()), None)
            if matched:
                table_name = matched
            else:
                continue

        # 2. Extract WHERE Equality / Range Columns
        where_match = re.search(r"\bWHERE\b(.*?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|;|$)", sql, re.IGNORECASE | re.DOTALL)
        where_clause = where_match.group(1) if where_match else ""

        target_cols = []
        if where_clause:
            col_matches = re.findall(r"[`\"]?([a-zA-Z0-9_]+)[`\"]?\s*(?:=|<|>|IN|LIKE|BETWEEN)", where_clause, re.IGNORECASE)
            for c in col_matches:
                c_clean = c.strip("`\"'")
                if c_clean.lower() not in ("select", "and", "or", "not", "null", "case") and c_clean not in target_cols:
                    target_cols.append(c_clean)

        # 3. Extract ORDER BY Columns
        order_match = re.search(r"\bORDER\s+BY\s+[`\"]?([a-zA-Z0-9_]+)[`\"]?", sql, re.IGNORECASE)
        if order_match:
            order_col = order_match.group(1).strip("`\"'")
            if order_col not in target_cols:
                target_cols.append(order_col)

        if target_cols:
            cols_to_index = target_cols[:3]  # composite up to 3 cols
            idx_name = f"idx_{table_name}_{'_'.join(cols_to_index)}"[:60]
            rec_key = f"{table_name}:{','.join(cols_to_index)}"

            if rec_key in seen_recommendations:
                continue
            seen_recommendations.add(rec_key)

            if dialect in ("mysql", "mariadb"):
                cols_str = ", ".join(f"`{c}`" for c in cols_to_index)
                ddl = f"CREATE INDEX `{idx_name}` ON `{table_name}` ({cols_str});"
            elif dialect == "postgresql":
                cols_str = ", ".join(f'"{c}"' for c in cols_to_index)
                ddl = f'CREATE INDEX "{idx_name}" ON "{table_name}" ({cols_str});'
            else:
                cols_str = ", ".join(f'"{c}"' for c in cols_to_index)
                ddl = f'CREATE INDEX "{idx_name}" ON "{table_name}" ({cols_str});'

            ratio = item.get("inefficiency_ratio", 1.0)
            est_gain = "80–95% Latency Reduction" if ratio > 50 else ("50–80% Latency Reduction" if ratio > 10 else "20–40% Speedup")

            recommendations.append({
                "table": table_name,
                "index_name": idx_name,
                "columns": cols_to_index,
                "sql": ddl,
                "reason": f"Matches slow query pattern examined {item.get('rows_examined_avg', 0):,.0f} rows for {item.get('rows_sent_avg', 0):,.0f} rows sent (Inefficiency ratio: {ratio}x).",
                "estimated_gain": est_gain,
                "trigger_query": sql[:120] + ("…" if len(sql) > 120 else ""),
                "exec_count": item.get("exec_count", 1),
                "total_latency": item.get("total_latency", "N/A"),
            })

    return recommendations
