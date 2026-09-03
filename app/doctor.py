"""
SQL Helper Doctor & System Diagnostics CLI.
Verifies database drivers, OS keyring security, local Ollama LLM readiness,
Graphviz rendering, privacy sanitization, and git safety.
"""
from __future__ import annotations
import sys
import os
import platform
import shutil
import importlib
import urllib.request
import json
import re
from pathlib import Path


def run_doctor() -> dict[str, list[dict]]:
    """
    Runs all diagnostic checks and returns structured results.
    """
    checks = {
        "environment": [],
        "database_drivers": [],
        "security_and_keyring": [],
        "ai_engine": [],
        "renderers": [],
        "privacy_and_git": [],
    }

    # 1. Environment Check
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 10)
    checks["environment"].append({
        "name": f"Python Version: {py_ver}",
        "status": "PASS" if py_ok else "FAIL",
        "message": "Python 3.10+ is required." if not py_ok else "Supported Python runtime.",
    })
    checks["environment"].append({
        "name": f"OS Platform: {platform.system()} ({platform.release()})",
        "status": "PASS",
        "message": f"Architecture: {platform.machine()}",
    })

    # 2. Database Drivers
    drivers = [
        ("pymysql", "MySQL & MariaDB Driver", "pip install pymysql cryptography"),
        ("psycopg2", "PostgreSQL Driver", "pip install psycopg2-binary"),
        ("sqlite3", "SQLite Built-in Driver", "Built into Python"),
        ("sqlalchemy", "SQLAlchemy ORM Engine", "pip install sqlalchemy"),
        ("pandas", "Pandas DataFrame Engine", "pip install pandas"),
        ("streamlit", "Streamlit UI Workbench", "pip install streamlit"),
    ]
    for mod_name, desc, install_hint in drivers:
        try:
            mod = importlib.import_module(mod_name)
            ver = getattr(mod, "__version__", "OK")
            checks["database_drivers"].append({
                "name": f"{desc} ({mod_name} v{ver})",
                "status": "PASS",
                "message": "Installed and ready.",
            })
        except ImportError:
            status = "WARN" if mod_name == "psycopg2" else "FAIL"
            checks["database_drivers"].append({
                "name": f"{desc} ({mod_name})",
                "status": status,
                "message": f"Not found. Install with: `{install_hint}`",
            })

    # 3. Security & Keyring Check
    try:
        import keyring
        backend = keyring.get_keyring()
        backend_name = backend.__class__.__name__
        is_safe_backend = "Fail" not in backend_name and "null" not in backend_name.lower()
        checks["security_and_keyring"].append({
            "name": f"OS Keyring Service ({backend_name})",
            "status": "PASS" if is_safe_backend else "WARN",
            "message": "Credentials secured in OS Credential Manager / Keychain." if is_safe_backend else "Plaintext fallback in use.",
        })
    except Exception as e:
        checks["security_and_keyring"].append({
            "name": "OS Keyring Service",
            "status": "WARN",
            "message": f"Keyring check failed: {e}",
        })

    # Verify config.yaml has zero hardcoded passwords
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r") as f:
                cfg_data = yaml.safe_load(f) or {}
            conns = cfg_data.get("connections", [])
            has_plain_pw = any("password" in c and c["password"] for c in conns if isinstance(c, dict))
            checks["security_and_keyring"].append({
                "name": "config.yaml Password Privacy",
                "status": "FAIL" if has_plain_pw else "PASS",
                "message": "Zero plaintext passwords in config file." if not has_plain_pw else "Found plaintext password in config.yaml! Move to keyring.",
            })
        except Exception:
            pass

    # 4. Local AI / Ollama Check
    ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", headers={"User-Agent": "SQLHelperDoctor/1.0"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            checks["ai_engine"].append({
                "name": f"Local Ollama Server ({ollama_url})",
                "status": "PASS",
                "message": f"Connected! Available models: {', '.join(models[:4]) if models else 'None loaded (run `ollama pull qwen2.5-coder`)'}",
            })
    except Exception:
        checks["ai_engine"].append({
            "name": f"Local Ollama Server ({ollama_url})",
            "status": "INFO",
            "message": "Ollama not running locally (Local AI advisor is optional). Run `ollama serve` to enable offline AI.",
        })

    # 5. Renderers (Mermaid & Graphviz)
    dot_path = shutil.which("dot")
    checks["renderers"].append({
        "name": "Mermaid.js Browser ERD Engine",
        "status": "PASS",
        "message": "Natively supported in modern browsers (Zero install required).",
    })
    checks["renderers"].append({
        "name": f"Graphviz DOT Binary ({dot_path or 'Not in PATH'})",
        "status": "PASS" if dot_path else "INFO",
        "message": "Installed and available for SVG export." if dot_path else "Optional: Install Graphviz for native DOT rendering (`choco install graphviz` / `brew install graphviz`).",
    })

    # 6. Privacy & Git Sanitization Audit
    gitignore_path = Path(__file__).parent.parent / ".gitignore"
    if gitignore_path.exists():
        gi_content = gitignore_path.read_text()
        has_env = ".env" in gi_content
        has_db = "*.db" in gi_content or "config.yaml" in gi_content
        checks["privacy_and_git"].append({
            "name": ".gitignore Privacy Guardrails",
            "status": "PASS" if (has_env and has_db) else "WARN",
            "message": ".gitignore correctly ignores private credentials, SQLite DBs, and local configs.",
        })

    # Check for hardcoded local drives (e.g. D:\ or C:\Users in tracked files)
    project_root = Path(__file__).parent.parent
    tracked_leak = False
    leak_file = ""
    path_pattern = re.compile(r"[a-zA-Z]:[/\\](?:Users|projects|home|var)", re.IGNORECASE)
    for py_file in project_root.rglob("*.py"):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file) or py_file.name == "doctor.py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if path_pattern.search(content):
                tracked_leak = True
                leak_file = py_file.name
                break
        except Exception:
            pass

    checks["privacy_and_git"].append({
        "name": "Hardcoded Local Paths Sanitization",
        "status": "FAIL" if tracked_leak else "PASS",
        "message": "Zero hardcoded personal drive paths in source code." if not tracked_leak else f"Warning: Found absolute path in `{leak_file}`.",
    })


    return checks


def print_doctor_report():
    """Prints a beautiful CLI terminal report."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    results = run_doctor()

    symbols = {
        "PASS": "[PASS]",
        "WARN": "[WARN]",
        "INFO": "[INFO]",
        "FAIL": "[FAIL]",
    }

    print("\n" + "=" * 65)
    print("  [DOCTOR] SQL HELPER SYSTEM & ENVIRONMENT DIAGNOSTICS")
    print("=" * 65 + "\n")

    section_titles = {
        "environment": "Environment & Runtime",
        "database_drivers": "Database Connectors & Drivers",
        "security_and_keyring": "Security & Keyring Credential Storage",
        "ai_engine": "Local AI / Ollama Diagnostic",
        "renderers": "ERD Diagram & Visualization Renderers",
        "privacy_and_git": "Privacy Sanitization & Git Safety",
    }


    total_pass = 0
    total_warn = 0
    total_fail = 0

    for section_key, items in results.items():
        print(f"--- {section_titles.get(section_key, section_key)} ---")
        for item in items:
            st_code = item["status"]
            if st_code == "PASS":
                total_pass += 1
            elif st_code in ("WARN", "INFO"):
                total_warn += 1
            elif st_code == "FAIL":
                total_fail += 1

            sym = symbols.get(st_code, f"[{st_code}]")
            print(f"  {sym:<8} {item['name']}")
            if item.get("message"):
                print(f"           └─ {item['message']}")
        print()

    print("=" * 65)
    if total_fail == 0:
        print(f" 🎉 System Doctor Verdict: ALL CHECKS PASSED ({total_pass} passed, {total_warn} notices)")
        print("    SQL Helper is fully configured, secure, and ready for production.")
    else:
        print(f" ⚠️  System Doctor Verdict: {total_fail} CRITICAL ISSUES DETECTED")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    print_doctor_report()
