# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

lsfMonitor is an HPC cluster monitoring tool for IBM LSF, OpenLava, and Volclava. It samples cluster metrics (jobs, queues, hosts, load, utilization, licenses) into SQLite databases and displays them via a PyQt5 desktop GUI. It also includes an ML-based job memory prediction subsystem (memPrediction) and an LLM-powered AI helpdesk with RAG.

**Language:** Python 3.12.12
**GUI Framework:** PyQt5; light theme via custom QSS in `gui/theme.py`, dark mode (`-d`) via qdarkstyle
**No test suite exists** — there are no tests, pytest config, or CI/CD pipelines.

## Installation & Running

```bash
# Install dependencies
pip install -r requirements.txt

# Install (generates shell wrappers and config)
python3 install.py [-p PREFIX] [-f] [-m]
#   -p PREFIX : install path (default: current directory)
#   -f        : force reinstall (overwrites config)
#   -m        : also install memPrediction subsystem

# Launch GUI
./bin/bmonitor

# Sample data (typically via crontab)
./bin/bsample -c   # cleanup expired data (config_lsf.cleanup_expire_days)
./bin/bsample -j   # jobs
./bin/bsample -m   # memory & idle_factor
./bin/bsample -q   # queues
./bin/bsample -qH  # queue-host mapping
./bin/bsample -gH  # host group-host mapping
./bin/bsample -H   # host status
./bin/bsample -l   # load
./bin/bsample -u   # user job stats
./bin/bsample -U   # utilization
./bin/bsample -UD  # utilization daily
./bin/bsample -A   # AI cluster analysis report

# License sampling (typically via crontab)
./bin/license_sample -c   # cleanup expired data (config_license.cleanup_expire_days)
./bin/license_sample -u   # feature usage snapshot
./bin/license_sample -U   # feature utilization stats

# Recommended crontab (see bin/bsample.py & bin/license_sample.py epilogs):
#   */5  * * * * bsample -m -q -H -l          # high freq: mem/queue/host/load
#   */30 * * * * bsample -qH -gH             # mid freq: mappings
#   */30 * * * * license_sample -u -U        # mid freq: license usage + util
#   10 11,23 * * * bsample -j                # low freq: done jobs
#   30 11,23 * * * bsample -u                # low freq: user stats
#   55 23 * * * bsample -UD                  # daily: utilization daily
#   3  0 * * * bsample -c && license_sample -c   # daily: cleanup
```

## Architecture

### Core Module Layout

- **`bin/`** — Entry points:
  - `bmonitor.py` — main GUI launcher (injects `--panel=lsf`, delegates to `gui.main_window.main`)
  - `license_monitor.py` — GUI launcher focused on the License panel (`--panel=license`)
  - `bsample.py` — LSF data sampler (jobs/queues/hosts/load/users/utilization, via `multiprocessing.Process` parallelism)
  - `license_sample.py` — license (lmstat) data sampler
  - `bmonitor_cli.py` — JSON-output CLI for ICMate/script integration (real-time + `db` historical subcommands)
- **`common/`** — Shared libraries, the most important modules:
  - `common.py` — Generic utilities (colored print `bprint`, subprocess `run_command`, directory/file creation `create_dir`/`create_file`, CSV, ssh client, safe expression evaluator `safe_eval_expr`, multi-keyword matcher `match_exact_or_fuzzy` for exact-preferred fuzzy filtering)
  - `common_config.py` — Unified config loader for the four panels: reads `config_<name>.py` from `config/`, overlaid by `~/.lsfMonitor/config/config_<name>.py`, loaded into `sys.modules` via importlib; also cluster-specific reload (`reload_config_for_cluster`)
  - `common_db_path.py` — Resolves a subsystem's db_path with fallback: `config_<name>.db_path` > `config.db_path/<name>` > `<install>/db/<name>`; also `ensure_db_root` (chmod db root 0o1777)
  - `common_lsf.py` — Parses LSF/volclava/openlava commands: `bjobs`/`bjobs -UF`, `bqueues`, `bhosts`/`bhosts -l`, `lsload`, `lshosts`, `busers`, `lsid`, `bmgroup`, queue-host/host-group mappings, `bhist`, `bpeek`; `get_exec_host_list` parses multi-host EXEC_HOST strings ("1*hostA:1*hostB")
  - `common_license.py` — FlexLM/lmstat license parsing (`get_license_info` → server status/version/feature issued/in_use/expires), `FilterLicenseDic` filters (exact-preferred fuzzy match via `match_exact_or_fuzzy`, multi-keyword), time/expiry helpers
  - `common_sqlite3.py` — SQLite CRUD: connect with busy_timeout + stale-journal handling, create/insert/update tables, column-oriented query; new db files forced 0o644
  - `common_app_log.py` — Tool launch log: each GUI launch appends to shared `db/log/log.db` (per-user table `app_log_<user>`), best-effort with `~/.lsfMonitor` fallback
  - `common_run_log.py` — RUN panel command history: shared `db/run/run_log.db` (per-user table `run_history_<user>`), with `~/.lsfMonitor` fallback
  - `common_pyqt5.py` — PyQt5 widget helpers (auto-resize tables, readonly tables, `MultiValueCompleter` for space-separated multi-value completion, matplotlib canvas styling, custom widgets)
  - `common_ai.py` — LLM agent with tool-use (OpenAI and Anthropic APIs), RAG doc loader, cluster metrics, AI Analyze HTML report generator
- **`config/`** — Configuration (Python modules, generated at install unless noted):
  - `config.py` — top-level default config holding the shared `db_path` (generated by `install.py:gen_default_config_file`)
  - `config_lsf.py` / `config_license.py` / `config_run.py` / `config_ai.py` — per-panel configs, each importable as a Python module via `common_config.load_config('<name>')`; per-panel `db_path` empty → falls back to `config.py`'s `<db_path>/<name>`
  - `config/lsf/` — `exit_code.yaml`, `term_signal.yaml` (used by `bmonitor_cli job --diagnose fail` and AI to interpret exit codes / term signals)
  - `config/license/` — license subsystem data files (`LM_LICENSE_FILE`, utilization filters) generated at install
  - `config/ai/` — AI skill definitions, each subdir is a standalone skill: `lsf_job_pend`, `lsf_job_slow`, `lsf_job_fail`, `lsfmonitor_usage`
- **`gui/`** — PyQt5 GUI:
  - `main_window.py` — unified `MainWindow` with four sidebar panels (LSF / LICENSE / RUN / AI), eager panel construction + lazy UI build
  - `lsf_panel.py` / `license_panel.py` / `run_panel.py` / `ai_panel.py` — the four sidebar panels
  - `panel_base.py` — base class (lazy inner-tab build via `on_first_show`, shared menubar hooks)
  - `app_context.py` — `AppContext` holds shared state across panels (cluster, db paths, sibling references)
  - `run_executor.py` — background executor for the RUN panel (ssh + pexpect, per-host concurrent with timeout)
  - `theme.py` — color constants + light QSS; dark mode via qdarkstyle
  - `version.py` — single source of truth for VERSION / RELEASE_DATE / USER
- **`tools/`** — CLI tools:
  - `akill` — enhanced `bkill` (filter by jobid/job_name/command/host/queue/user)
  - `check_issue_reason` — standalone job issue diagnosis (predates AI skills)
  - `gen_LM_LICENSE_FILE` — generate/refresh the license server list from module config
  - `lmstat` — bundled lmstat binary shim
  - `message` — non-blocking loading prompt dialog (called by GUI ShowMessage subprocess)
  - `patch` — patch/upgrade tool (sync new files, config migration)
  - `process_tracer` — trace a job's process tree on execution hosts
  - `rag_builder` — build/update the RAG vector database from user docs (PDF/txt/md/rst)
  - `seedb` — read-only SQLite query helper for the sampling DBs
- **`db/`** — runtime data root (created by `install.py:ensure_db_root`, chmod 0o1777). Subdirs created on first use: `lsf/<cluster>/`, `license/license_server/<server>/<vendor>/`, `ai/` (+ `ai/rag/`, `ai/ai_report/<kind>/`), `log/`, `run/`. Permission spec: `docs/lsfMonitor_user_manual.md` 附3.
- **`lib/`** — bundled `libsqlite3.so.0` (prepended to `LD_LIBRARY_PATH` by shell wrappers)
- **`docs/`** — User manuals (`lsfMonitor_user_manual.md`, `memPrediction_user_manual.md`), screenshots (`docs/images/`)

### memPrediction Subsystem

- **`memPrediction/bin/`** — `sample.py` (collect job rusage), `train.py` (XGBoost training), `predict.py` (memory prediction), `report.py` (generate prediction reports)
- **`memPrediction/common/`** — ML pipeline utilities: `common.py`, `common_es.py` (Elasticsearch), `common_model.py`; `common_lsf.py` and `common_sqlite3.py` are symlinks to the top-level `common/` versions
- **`memPrediction/config/`** — `training.config.yaml` (XGBoost/embedding hyperparameters), `web_app.yaml` (web service config), `rusage_report_template.md`
- **`memPrediction/tools/`** — `predict_web.py` (web-based prediction entry), `update.py`
- **`memPrediction/web_app/`** — Flask backend (`backend/`) + React/TypeScript frontend (`frontend/`, rsbuild, NextUI, Recharts)
- **`memPrediction/db/`** — `job_db/`, `model_db/`, `report_db/` (sampled/trained data)
- **`memPrediction/lib/`** — bundled shared libs

### Key Patterns

1. **Environment variable coupling:** All tools depend on `LSFMONITOR_INSTALL_PATH` (set by generated shell wrappers). User config can be overridden at `~/.lsfMonitor/config/config_<name>.py`.
2. **Config as Python module:** Each `config/config_<name>.py` is imported directly — it defines variables like `db_path`, `lmstat_path`, AI API settings. Loaded via `common/common_config.py`'s `load_config(name)`.
3. **bmonitor GUI structure:** Unified `MainWindow` with four sidebar panels (LSF / LICENSE / RUN / AI). Expensive tabs use lazy loading via `panel_base.py`'s `on_first_show` callback.
4. **bsample parallelism:** Uses `multiprocessing.Process` to run different sampling tasks concurrently.
5. **AI agent:** Multi-turn tool-use loop in `common_ai.py` exposing tools: `run_command`, `query_license_info`, `query_job_history`, `search_documentation` (FAISS-based RAG). Commands are classified as forbidden/dangerous with user confirmation for dangerous ops.
6. **SQLite databases:** Stored under `db_path` (from config), organized by cluster name, date, and metric type.
7. **RAG builder:** `tools/rag_builder.py` builds/updates the RAG vector database (`rag_chunks.json` + `rag_faiss.index`) from user documents (PDF/txt/md/rst). Supports append mode and configurable output directory/file prefix (`-o`, `--prefix`). See `lsfMonitor_user_manual.md` 第 5 章 for usage.
8. **AI Analyze reports:** One-shot AI health reports (vs. the conversational AI Helpdesk). In `common_ai.py`: `collect_cluster_snapshot()` gathers a read-only snapshot, `generate_cluster_analyze_report()` runs the agent loop (read-only, `confirm_mode='auto_reject'`) and emits a self-contained HTML report. Triggered from the GUI AI panel's ANALYZE tab (four report kinds: cluster / user / job / queue, run via `AnalyzeReportThread` with a cancel button); the cluster report is also available via `bsample -A`. Reports land in `<ai_db_path>/ai_report/<kind>/*.html` (resolved via `common_db_path.resolve_db_path`).

### Documentation

- **`docs/lsfMonitor_user_manual.md`** — Full user manual in Chinese (installation, config, GUI panels, bmonitor_cli, auxiliary tools, FAQ, exit codes, db_path permission spec in 附3); images under `docs/images/`
- **`docs/memPrediction_user_manual.md`** — memPrediction ML subsystem manual
- Tool usage (akill/patch/rag_builder/seedb) is documented inside `lsfMonitor_user_manual.md` 第 5 章, not as standalone files

## Code Conventions

- File headers use `# -*- coding: utf-8 -*-` with a comment block containing file name, author, creation date, and description.
- Classes use `CamelCase`, methods/functions use `snake_case`.
- Heavy use of `os.path` for path manipulation (not `pathlib`).
- Print output uses custom `common.bprint()` with ANSI colors (levels: Debug/Info/Warning/Error/Fatal).
- LSF command output parsing is line-by-line with regex; see `common_lsf.py` for all parsers.
- Code blocks (`if`/`for`/`try`/`def`/`class`) have a blank line before and after the whole block (exception: the first statement inside a `def`/`class` needs no leading blank line).
- All Python code must pass `ruff check` (Ruff 0.15.12).
- **db_path permission spec:** `docs/lsfMonitor_user_manual.md` 附3 is the golden spec — shared-write dirs are `0o1777` (sticky), sampled/read-only dirs `0o755`, shared-write db files `0o666`, others `0o644`/`0o600`. `common.create_dir` re-chmods existing dirs to self-heal `cp -r`/umask drift.
