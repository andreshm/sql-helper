# 🗄️ SQL Helper — AI-Powered Database Performance & Storage Optimizer

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B.svg)](https://streamlit.io/)
[![MySQL](https://img.shields.io/badge/MySQL-8.0+-4479A1.svg)](https://www.mysql.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791.svg)](https://www.postgresql.org/)
[![SQLite](https://img.shields.io/badge/SQLite-3.35+-003B57.svg)](https://www.sqlite.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**SQL Helper** is an advanced, production-grade Database Performance Workbench, Storage Resizer, Single-Pass Data Type Optimizer, and AI Index Advisor for **MySQL / MariaDB**, **PostgreSQL**, and **SQLite**.

It combines **100% mathematically exact server-side SQL profiling** with **Local AI Semantic Auditing** (powered by Ollama `qwen2.5-coder:14b` or Cloud LLMs) to reclaim gigabytes of disk space, optimize RAM buffer pools, eliminate slow queries, and prevent future schema bugs.

---

## 🌟 Key Highlights

* 🎯 **Single-Pass Type Optimizer**: Downcasts integer ranges, shrinks oversized text, converts strings to ENUMs/Decimals, and sanitizes empty strings `''` $\rightarrow$ `NULL` in a single unified DDL execution.
* 🤖 **AI Semantic Risk Auditor**: Uses Local AI to double-check candidate migrations against business domain conventions (protecting UPS/FedEx tracking codes, international postal codes, and external IDs from invalid downcasting).
* ⚡ **Dual-Engine Index Advisor**: Combines deterministic static index rules with AI query analysis, safeguarded by live database catalog verification and index austerity convergence.
* 🧹 **Smart Compaction & Defragmentation**: Isolates fragmented tables to reclaim storage via `OPTIMIZE TABLE` and `VACUUM` with live before/after byte reclamation tracking.
* 🛡️ **Safety Guardrail & Execution Mode**: Read-only protection by default with 1-click execution mode toggling, individual query run buttons, and complete copyable `.sql` migration scripts.

---

## 📦 Core Feature Modules

| Module | Description |
| :--- | :--- |
| 📊 **Storage & Size Visualizer** | Interactive storage treemaps, table disk footprints, data vs index allocations, and bloat rankings. |
| 🧹 **Resize & Compaction** | Online InnoDB compaction, Smart Optimize for fragmented tables, and fast `ANALYZE TABLE` index statistics update. |
| ⚡ **Index Advisor** | Detects duplicate indexes, redundant prefixes, missing foreign key indexes, and low-cardinality index waste + AI composite advice. |
| 🔧 **Data Type Optimizer** | Full-table server-side SQL aggregates (MIN, MAX, string lengths, empty string sanitization) with 1-click batch execution and AI double-checks. |
| 🗂️ **Database Explorer** | Inspect tables, views, procedures, triggers, foreign key constraints, and download formatted DDL. |
| 📋 **Data Viewer** | High-performance paginated data grid with filters, row counters, and CSV/JSON exports. |
| 🛠️ **Query Builder & Workbench** | SQL editor with execution plans (`EXPLAIN`), multi-statement support, and DML template generators. |
| 🔒 **Security Analyzer** | Audit unencrypted sensitive columns, missing primary keys, dynamic SQL injection risks, and broad grants. |
| 🔍 **NL → SQL Generator** | Plain-English prompt to dialect-optimized SQL with instant schema validation and execution. |

---

## 🚀 Quick Start Guide

### Prerequisites
* Python 3.10 or higher
* (Optional for AI) [Ollama](https://ollama.com/) with `qwen2.5-coder:14b`

### 1. Clone & Setup
```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/sql-helper.git
cd sql-helper

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration (Optional)
Copy the example configuration file:
```bash
cp config.example.yaml config.yaml
```

If using **Ollama (Recommended)**, start Ollama and pull the model:
```bash
ollama pull qwen2.5-coder:14b
```

### 3. Launch the Application
```bash
# On Windows:
run.bat

# Or directly with Streamlit:
streamlit run main.py
```
Open **http://localhost:8501** in your browser.

---

## 🤖 AI Providers

SQL Helper supports both 100% private Local AI and Cloud AI providers via `config.yaml` or environment variables:

| Provider | Model Name | Configuration |
| :--- | :--- | :--- |
| **Ollama (Local / Private)** | `qwen2.5-coder:14b` (Default) | `base_url: http://localhost:11434` |
| **Anthropic** | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY=sk-...` |
| **OpenAI** | `gpt-4o` | `OPENAI_API_KEY=sk-...` |
| **Google Gemini** | `gemini-1.5-pro` | `GEMINI_API_KEY=AI...` |

---

## 🛡️ Safety & Integrity Architecture

1. **Read-Only Guardrail**: All destructive DDL (`ALTER TABLE`, `DROP INDEX`, `OPTIMIZE TABLE`) is blocked by default. You can enable **Execution Mode** with 1 click to apply fixes.
2. **Auto-Increment Protection**: Ensures `AUTO_INCREMENT` primary key definitions are preserved without dropping sequence counters.
3. **Leading Zero Integrity**: Safeguards formatted numeric strings (like ZIP codes `'01234'`, telephone numbers, and PINs) from invalid integer downcasting.
4. **Pre-Flight Nullability Unlocking**: Prevents MySQL Error 1048 when transforming empty strings (`''`) to `NULL` on `NOT NULL` columns.

---

## 🧪 Testing

Run the automated unit and integration test suite:
```bash
python -m unittest discover -s tests -p "test_*.py"
```

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/YOUR_USERNAME/sql-helper/issues).
