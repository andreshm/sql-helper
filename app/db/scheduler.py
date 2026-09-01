"""
Automated Database Maintenance Scheduler & Crontab / Task Generator.
Generates production-grade Linux cron jobs, Windows Task Scheduler scripts,
and in-app scheduled maintenance tasks.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def build_cron_expression(
    frequency: str,
    hour: int = 3,
    minute: int = 0,
    day_of_week: int = 0,  # 0 = Sunday
    day_of_month: int = 1,
) -> str:
    """
    Returns standard 5-field cron syntax (minute hour day-of-month month day-of-week).
    """
    if frequency == "Daily":
        return f"{minute} {hour} * * *"
    elif frequency == "Weekly":
        return f"{minute} {hour} * * {day_of_week}"
    elif frequency == "Monthly":
        return f"{minute} {hour} {day_of_month} * *"
    elif frequency == "Hourly":
        return f"{minute} * * * *"
    return f"{minute} {hour} * * 0"


def generate_crontab_script(
    dialect: str,
    host: str = "localhost",
    port: int = 3306,
    user: str = "root",
    database: str = "",
    action: str = "smart_optimize",  # smart_optimize | full_vacuum | analyze_table
    cron_expr: str = "0 3 * * 0",
) -> str:
    """
    Generates ready-to-use Linux crontab entry and bash script.
    """
    lines = [
        f"#!/bin/bash",
        f"# ========================================================",
        f"# SQL Helper: Automated Database Maintenance Script",
        f"# Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Engine: {dialect.upper()} | Target Database: {database or 'All'}",
        f"# Schedule: {cron_expr}",
        f"# ========================================================",
        f"",
        f"LOG_FILE=\"/var/log/sql_helper_maintenance_{database or 'all'}.log\"",
        f"echo \"[$(date)] Starting maintenance action: {action}\" >> \"$LOG_FILE\"",
        f"",
    ]

    if dialect in ("mysql", "mariadb"):
        if action == "smart_optimize":
            lines.extend([
                f"# Run mysqlcheck --optimize on all tables in {database or 'all databases'}",
                f"mysqlcheck -h {host} -P {port} -u {user} -p$DB_PASSWORD --optimize {database or '--all-databases'} >> \"$LOG_FILE\" 2>&1",
            ])
        elif action == "analyze_table":
            lines.extend([
                f"# Run mysqlcheck --analyze to update optimizer statistics",
                f"mysqlcheck -h {host} -P {port} -u {user} -p$DB_PASSWORD --analyze {database or '--all-databases'} >> \"$LOG_FILE\" 2>&1",
            ])
        else:
            lines.extend([
                f"# Run mysqlcheck --auto-repair and optimize",
                f"mysqlcheck -h {host} -P {port} -u {user} -p$DB_PASSWORD --auto-repair --optimize {database or '--all-databases'} >> \"$LOG_FILE\" 2>&1",
            ])
    elif dialect == "postgresql":
        if action == "smart_optimize" or action == "full_vacuum":
            lines.extend([
                f"# Run vacuumdb with analyze and freeze",
                f"PGPASSWORD=\"$DB_PASSWORD\" vacuumdb -h {host} -p {port} -U {user} -d {database or 'postgres'} --analyze --verbose >> \"$LOG_FILE\" 2>&1",
            ])
        else:
            lines.extend([
                f"# Refresh optimizer statistics only",
                f"PGPASSWORD=\"$DB_PASSWORD\" vacuumdb -h {host} -p {port} -U {user} -d {database or 'postgres'} --analyze-only >> \"$LOG_FILE\" 2>&1",
            ])
    else:  # sqlite
        lines.extend([
            f"# Rebuild SQLite file and truncate WAL",
            f"sqlite3 \"{database or 'database.db'}\" \"VACUUM; PRAGMA wal_checkpoint(TRUNCATE); ANALYZE;\" >> \"$LOG_FILE\" 2>&1",
        ])

    lines.extend([
        f"",
        f"echo \"[$(date)] Maintenance complete.\" >> \"$LOG_FILE\"",
        f"",
        f"# --- Linux Crontab Entry ---",
        f"# Add the following line to your crontab (crontab -e):",
        f"# {cron_expr} /path/to/this_script.sh",
    ])

    return "\n".join(lines)


def generate_windows_task_script(
    dialect: str,
    host: str = "localhost",
    port: int = 3306,
    user: str = "root",
    database: str = "",
    action: str = "smart_optimize",
) -> str:
    """
    Generates a Windows Task Scheduler batch file (.bat).
    """
    lines = [
        "@echo off",
        "rem ========================================================",
        "rem SQL Helper: Automated Windows Maintenance Script",
        f"rem Engine: {dialect.upper()} | Target Database: {database or 'All'}",
        "rem ========================================================",
        "",
        "set LOG_FILE=%~dp0maintenance.log",
        "echo [%date% %time%] Starting maintenance action: " + action + " >> \"%LOG_FILE%\"",
        "",
    ]

    if dialect in ("mysql", "mariadb"):
        db_arg = database if database else "--all-databases"
        opt_flag = "--optimize" if action == "smart_optimize" else ("--analyze" if action == "analyze_table" else "--auto-repair --optimize")
        lines.append(f"mysqlcheck.exe -h {host} -P {port} -u {user} {opt_flag} {db_arg} >> \"%LOG_FILE%\" 2>&1")
    elif dialect == "postgresql":
        db_name = database if database else "postgres"
        lines.append(f"vacuumdb.exe -h {host} -p {port} -U {user} -d {db_name} --analyze >> \"%LOG_FILE%\" 2>&1")
    else:
        lines.append(f"sqlite3.exe \"{database or 'database.db'}\" \"VACUUM; PRAGMA wal_checkpoint(TRUNCATE); ANALYZE;\" >> \"%LOG_FILE%\" 2>&1")

    lines.extend([
        "",
        "echo [%date% %time%] Maintenance completed successfully. >> \"%LOG_FILE%\"",
    ])
    return "\r\n".join(lines)


def get_maintenance_jobs() -> list[dict]:
    """Retrieves saved recurring maintenance schedules from config.yaml."""
    if not _CONFIG_PATH.exists():
        return []
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg.get("maintenance_jobs", [])
    except Exception:
        return []


def save_maintenance_job(job: dict) -> list[dict]:
    """Saves a maintenance job specification to config.yaml."""
    cfg = {}
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

    jobs = cfg.get("maintenance_jobs", [])
    if not job.get("id"):
        job["id"] = f"job_{uuid.uuid4().hex[:8]}"
    job["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    jobs.append(job)
    cfg["maintenance_jobs"] = jobs

    try:
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass

    return jobs


def delete_maintenance_job(job_id: str) -> list[dict]:
    """Deletes a maintenance schedule from config.yaml."""
    if not _CONFIG_PATH.exists():
        return []
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return []

    jobs = [j for j in cfg.get("maintenance_jobs", []) if j.get("id") != job_id]
    cfg["maintenance_jobs"] = jobs

    try:
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    except Exception:
        pass

    return jobs
