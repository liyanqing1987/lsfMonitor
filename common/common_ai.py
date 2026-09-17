# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import shlex
import datetime
import threading

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']))
from common import common
from common import common_license
from common import common_sqlite3

# openai and anthropic are lazy-imported inside their respective methods
# (_agent_loop_openai / _agent_loop_anthropic) to avoid ~4.8s startup penalty.
# They are only needed when the user actually starts an AI chat session.

# Default dangerous commands that require user confirmation.
DEFAULT_DANGEROUS_COMMANDS = ['bkill', 'badmin', 'brestart', 'bstop', 'bresume', 'bswitch', 'bmod',
                              'rm', 'kill', 'killall', 'shutdown', 'reboot', 'mkfs', 'dd',
                              'eval', 'source', 'xargs']

SYSTEM_PROMPT = """You are the AI assistant of lsfMonitor — a unified IC infrastructure monitoring & operations platform integrating three domains:

1. HPC cluster monitoring (IBM LSF / OpenLava / Volclava): jobs, queues, hosts, load, utilization.
2. EDA license monitoring (FlexNet): feature usage, expiry, utilization.
3. Batch operations: run shell commands across hosts over passwordless ssh (host inventory from LSF bhosts).

Tools available:
- run_command(LSF/Linux/shell commands on the cluster)
- query_license_info(EDA license feature/user/server usage)
- query_job_history(finished LSF job records by job_id/user/queue/status/date)
- search_documentation(RAG over indexed EDA/IT docs)

`bmonitor_cli` is a CLI (call via run_command) that outputs structured JSON for LSF/License queries — it covers BOTH real-time cluster state (jobs/hosts/queues/users/license) and historical sampled trends, and its output is pre-parsed and stable across LSF/OpenLava/Volclava. Prefer `bmonitor_cli` over raw LSF commands (bjobs/bhosts/bqueues/lsload/lshosts) whenever it can answer the question; fall back to raw LSF only when `bmonitor_cli` lacks the needed capability. To discover available subcommands and their arguments, run `bmonitor_cli --help` (or `bmonitor_cli <topic> --help`).

Diagnostic habit — prefer the narrowest query that answers the question. For "why is job <id> PEND/SLOW/FAIL", run `bmonitor_cli job <id> --diagnose pend|slow|fail` first and answer from its JSON output; do NOT pre-collect cluster-wide dumps before checking the single job's own reason. Escalate to broader queries only when the single-job answer is genuinely insufficient. For BROAD/cluster-wide questions, prefer `bmonitor_cli`'s aggregate subcommands (e.g. `cluster-summary`, `pending-reasons`) over running several basic queries (hosts/queues/users) separately and recomputing by hand.

Response style:
- Be concise and direct. Lead with the conclusion or diagnosis, then explain briefly.
- Reply in user's language.
- If a command fails or returns an error, immediately retry with an alternative command. Not all flags are supported across LSF/OpenLava/Volclava — use simpler, universally compatible flags on retry. If a tool fails consecutively twice, stop retrying: tell the user the tool is temporarily unavailable and give the information already collected.
- If a command output is truncated, analyze the available portion first. If the truncated data is insufficient, prefer `bmonitor_cli`'s built-in filter parameters (e.g. --user/--queue/--status); use pipe commands (grep/awk/sort/head) only when the CLI's own filtering is not enough, rather than re-running the same command.
- For greetings (hello/hi/你好) or general questions, respond conversationally — briefly introduce your capabilities across the three domains above without executing any commands. Only run commands when the user asks a specific question about their jobs, cluster, licenses, or resources.
- During diagnostic/information-gathering phases (querying job info, checking queue status, license usage, collecting cluster data), you MUST execute commands directly via run_command tool — never list them as text for the user to choose.

=== Hard constraint (highest priority) ===
NEVER execute `ssh` (or `scp`/`sftp`) via run_command. SSH may prompt interactively (e.g. host key confirmation "Are you sure you want to continue connecting (yes/no)?"), which blocks the tool and is invisible in the GUI. Use LSF commands or bmonitor_cli instead. Cross-host batch operations are handled by the RUN panel's dedicated ssh channel.

=== Dangerous commands (two distinct cases) ===
The tool auto-detects dangerous commands (bkill/badmin/bstop/bresume/brestart/bswitch/bmod/rm/kill/...). There are TWO cases — do not confuse them:

(1) Single command the user explicitly asked you to run (e.g. "kill my job 123", "bmod -R ... 123"):
    Call run_command directly. The tool pops up a confirmation dialog; if the user APPROVES it, the command executes normally. Do NOT refuse an explicitly-requested dangerous command — the user's approval in the dialog IS the authorization.

(2) Repair steps YOU propose after diagnosis (the user did NOT explicitly ask to change anything):
    Do NOT execute them directly. Present them as a numbered "可选操作" list and wait for the user to pick one by number before executing:
---
**可选操作：**
1. <action description>
2. <action description>

请回复数字选择操作，或直接提问。
---
Only case (2) requires the numbered-list-then-confirm flow. Case (1) only needs the dialog approval — never block an explicitly-authorized command.
"""

CLUSTER_ANALYZE_SYSTEM_PROMPT = """You are a senior LSF/OpenLava/Volclava HPC cluster operations expert generating a cluster analysis report.

## Background

The user message provides two inputs:
- (A) Authoritative metrics already computed by the system: host state distribution, slots total/used/idle, job counts, slot/cpu/mem utilization, and per-queue slots/total/pend/run/pend-rate.
- (B) A raw cluster snapshot: bqueues -w/-l, bhosts -w, lshosts -w, lsload, busers all, bjobs -u all -w, bjobs -u all -p.

The report's data sections (集群现状/主机状态/队列负载) are ALREADY generated by the system — you MUST NOT output them again.

## Your job

Produce ONLY two sections — <h2>集群问题</h2> and <h2>分析汇总</h2> — analyzing exactly three dimensions:
1. **Jobs**: PEND buildup root cause, RUN jobs that are zombie/abnormal, EXIT failure patterns (OOM / timeout / command error).
2. **Queues**: slot utilization, whether PEND buildup is a limit factor or resource shortage, member-host state.
3. **Hosts/Load/Utilization**: closed_Busy/closed_Full host counts & distribution, high-load hosts, whether memory is tight.

## Data usage rules

- Every number must be based on input (A); treat it as ground truth and NEVER recompute or contradict it.
- In almost all cases write DIRECTLY from (A)+(B) WITHOUT calling any tool; only call a read-only command (e.g. bjobs, bqueues -l, bhosts -l) if a SPECIFIC fact you must cite is genuinely missing.
- This is a **read-only analysis report**: do NOT execute any state-changing commands (bkill/badmin/bstop/bresume/brestart/bswitch/bmod, rm/kill/reboot, etc.) — they are auto-rejected.
- DO NOT analyze EDA license; never call query_license_info.

## Hard rules

1. **Evidence first**: only report a problem CONFIRMED by concrete data (a count, a host name, a queue, a job id, a metric). Never speculate or invent hypothetical problems. If a dimension's data is missing, state "数据不足" in the corresponding card — do not speculate.
2. **Executable solutions**: every "问题解决" must give a concrete, actionable directive and state explicitly whether the 系统管理员 or the 用户 should act. When data supports a specific value, give it (e.g. "raise JL/U 50→100", "resubmit with -R rusage[mem=921600]", "bkill 12345"); when no concrete value can be derived from the data, give a directional suggestion grounded in the observed pattern (e.g. "consider raising the queue slot limit — current PEND suggests it is too low") rather than a vague "优化资源". Never fabricate numbers you cannot tie to input (A)/(B).
3. Reply in Chinese (中文).

## closed_Busy knowledge (common pitfall)

- closed_Busy is LSF automatic load control: hosts auto-close under high load and auto-reopen when resources free up — it **cannot** be recovered with `badmin hopen`.
- Only closed_Admin (manually closed by an admin) can be recovered with `badmin hopen`.
- In "系统管理员 TODO", do NOT suggest `badmin hopen` for closed_Busy hosts.

## Output format

Emit raw HTML fragment ONLY (no <html>/<body>, no markdown code fences, no <h1>). Output EXACTLY these two sections, in order:

<h2>集群问题</h2>
<!--
按严重度从高到低排列，分组顺序固定为 严重 → 中等 → 轻微。每个问题输出一张 issue 卡片，
卡片 class 用 issue high|mid|low（严重=high 红 / 中等=mid 橙 / 轻微=low 绿）。
每张卡片必须包含以下四部分：
  1) 标题行：<span class="badge high|mid|low">严重|中等|轻微</span> 后跟 <b>问题标题</b>
  2) <p><b>问题描述：</b>…</p>          —— 现象 + 数据依据（引用 (A) 中的精确数字/主机名/队列名/作业号）
  3) <p><b>问题分析：</b>…</p>          —— 根因
  4) <div><b>问题解决：</b>…</div>      —— 谁来解决（系统管理员/用户）+ 具体怎么做；命令/配置放 <pre><code>…</code></pre>
模板：
<div class="issue high">
  <p><span class="badge high">严重</span><b>问题标题</b></p>
  <p><b>问题描述：</b>……</p>
  <p><b>问题分析：</b>……</p>
  <div><b>问题解决：</b>由<u>系统管理员</u>处理：……<pre><code>…</code></pre></div>
</div>
若无任何确认的问题，本段仅输出 <p>未发现明显问题。</p>
-->

<h2>分析汇总</h2>
<!--
本段必须使用以下结构化卡片，不要写成长段落。每类卡片的 class 固定如下（用于按类型着色），不要改动 class：
  1) 一句整体结论：<div class="card-panel assess"><b>总体评估：</b>……（说明集群是否健康、最关键的一两个结论）</div>
  2) 严重问题清单：<div class="card-panel severe"><b>当前严重问题</b><ul><li>……</li></ul></div>（无则写“无”）
  3) 行动清单，左右两张卡片：
     <div class="two-col">
       <div class="panel admin"><div class="panel-h">系统管理员 TODO</div><ul><li>……</li></ul></div>
       <div class="panel user"><div class="panel-h">用户 TODO</div><ul><li>……</li></ul></div>
     </div>
语言精炼，每条 TODO 一句话、可执行。
-->
"""

# Tool definitions in OpenAI format (also used as canonical format).
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a command on the cluster and return its output. Use for LSF commands like bjobs, bqueues, bhosts, lsload, lshosts, busers, bkill, etc. Also supports common Linux commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command to execute, e.g. 'bjobs -u all -w', 'bqueues -w', 'bhosts -w'"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_license_info",
            "description": "Query EDA license usage information. Returns license server status, feature usage (issued/in_use), and user details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string", "description": "License feature name to filter (optional)"},
                    "user": {"type": "string", "description": "User name to filter (optional)"},
                    "server": {"type": "string", "description": "License server to filter (optional)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_job_history",
            "description": "Query historical finished job records from the local SQLite database. Jobs are stored in per-date DB files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Specific job ID to look up (optional)"},
                    "user": {"type": "string", "description": "Filter by user name (optional)"},
                    "queue": {"type": "string", "description": "Filter by queue name (optional)"},
                    "status": {"type": "string", "description": "Filter by job status: DONE, EXIT (optional)"},
                    "date": {"type": "string", "description": "Date to query in YYYYMMDD format (optional, default=today)"},
                    "limit": {"type": "integer", "description": "Max number of results (default=20)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_documentation",
            "description": "Search through local LSF/EDA documentation (user manuals, guides) for command syntax, options, configuration, error codes, best practices, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords, e.g. 'bsub memory limit', 'job array syntax'"}
                },
                "required": ["query"]
            }
        }
    }
]

# Tool definitions in Anthropic format.
TOOLS_ANTHROPIC = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"]
    }
    for t in TOOLS_OPENAI
]

MAX_OUTPUT_LENGTH = 4000


def detect_api_type(model_name):
    """Auto-detect API type from model name."""
    if 'claude' in model_name.lower():
        return 'anthropic'

    return 'openai'


def parse_xml_tool_calls(content):
    """Parse XML-formatted tool calls from model content (fallback for models that don't use function calling).

    Handles format:
        <function_calls>
        <invoke name="tool_name">
        <parameter name="param">value</parameter>
        </invoke>
        </function_calls>

    Returns list of dicts: [{'name': str, 'arguments': str(json)}] or empty list.
    """
    if '<function_calls>' not in content:
        return []

    results = []
    invoke_pattern = re.compile(r'<invoke\s+name="([^"]+)">(.*?)</invoke>', re.DOTALL)
    param_pattern = re.compile(r'<parameter\s+name="([^"]+)">(.*?)</parameter>', re.DOTALL)

    for match in invoke_pattern.finditer(content):
        tool_name = match.group(1)
        invoke_body = match.group(2)
        args = {}

        for param_match in param_pattern.finditer(invoke_body):
            args[param_match.group(1)] = param_match.group(2).strip()

        results.append({'name': tool_name, 'arguments': json.dumps(args, ensure_ascii=False)})

    return results


# ============================================================
# Tool execution functions (shared by both API types).
# ============================================================

def execute_command(command, dangerous_list, confirm_callback):
    """Execute a command with safety checks."""
    if not command.strip():
        return "Error: empty command."

    # Hard block: ssh/scp/sftp prompts interactively and blocks the tool
    # invisibly in the GUI. Tokenize the same way as the dangerous check below
    # so it can't hide behind separators/wrappers.
    ssh_tokens = [token for token in re.split(r'[\s|;&<>()`{}\'"$\\]+', command) if token]
    ssh_blocked = {'ssh', 'scp', 'sftp'}

    for token in ssh_tokens:
        if (token in ssh_blocked) or (os.path.basename(token) in ssh_blocked):
            return ("Error: ssh/scp/sftp is not allowed via run_command — it can "
                    "block the tool with an invisible interactive prompt. Use "
                    "LSF commands or bmonitor_cli instead. Cross-host batch "
                    "operations are handled by the RUN panel's dedicated ssh channel.")

    # Tokenize across all shell separators so dangerous commands can't hide
    # behind ;, &&, ||, |, newlines, $(...), backticks, quotes or wrappers
    # (xargs/sh -c/env/eval). Runs under shell=True.
    tokens = [token for token in re.split(r'[\s|;&<>()`{}\'"$\\]+', command) if token]

    for token in tokens:
        if (token in dangerous_list) or (os.path.basename(token) in dangerous_list):
            if not confirm_callback(command):
                return f"User rejected execution of command: {command}"

            break

    try:
        (return_code, stdout, stderr) = common.run_command(command)
        output = stdout.decode('utf-8', errors='replace') if stdout else ''

        if return_code != 0:
            err = stderr.decode('utf-8', errors='replace') if stderr else ''
            output = f"Command exited with code {return_code}.\nStdout:\n{output}\nStderr:\n{err}"

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + f"\n... (truncated, total {len(output)} chars. Analyze available data first. If insufficient, use pipe commands to filter precisely rather than re-running the same command.)"

        return output if output.strip() else "(no output)"
    except Exception as e:
        return f"Error executing command: {e}"


def execute_license_query(license_dic, lmstat_path='lmstat', bsub_command='', feature='', user='', server=''):
    """Query EDA license info."""
    try:
        if not license_dic:
            my_get_license_info = common_license.GetLicenseInfo(lmstat_path=lmstat_path, bsub_command=bsub_command)
            license_dic = my_get_license_info.get_license_info()

        if not license_dic:
            return "No license information available. Check LM_LICENSE_FILE and lmstat configuration."

        filtered_dic = common_license.FilterLicenseDic().run(
            license_dic,
            server_list=[server] if server else [],
            feature_list=[feature] if feature else [],
            user_list=[user] if user else []
        )

        lines = []

        for lic_server, server_info in filtered_dic.items():
            lines.append(f"Server: {lic_server} ({server_info.get('license_server_status', 'UNKNOWN')})")

            for vendor, vendor_info in server_info.get('vendor_daemon', {}).items():
                lines.append(f"  Vendor: {vendor} ({vendor_info.get('vendor_daemon_status', 'UNKNOWN')})")

                for feat, feat_info in vendor_info.get('feature', {}).items():
                    issued = feat_info.get('issued', '0')
                    in_use = feat_info.get('in_use', '0')
                    lines.append(f"    {feat}: {in_use}/{issued} in use")

                    for use_info in feat_info.get('in_use_info', []):
                        u = use_info.get('user', '')
                        host = use_info.get('execute_host', '')
                        start = use_info.get('start_time', '')
                        num = use_info.get('license_num', '1')
                        lines.append(f"      {u}@{host} ({num} license, since {start})")

        output = '\n'.join(lines) if lines else "No matching license information found."

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"

        return output
    except Exception as e:
        return f"Error querying license info: {e}"


def execute_job_history_query(db_path, job_id='', user='', queue='', status='', date='', limit=20):
    """Query historical job records from SQLite database."""
    try:
        job_db_path = str(db_path) + '/job'

        if not os.path.isdir(job_db_path):
            return f"Job database directory not found: {job_db_path}"

        if not date:
            date = datetime.datetime.now().strftime('%Y%m%d')

        db_file = str(job_db_path) + '/' + str(date) + '.db'

        if not os.path.exists(db_file):
            available = sorted([f.replace('.db', '') for f in os.listdir(job_db_path) if f.endswith('.db')])
            return f"No job database for date {date}. Available dates: {', '.join(available[-10:]) if available else 'none'}"

        conditions = []
        params = []

        if job_id:
            conditions.append("job=?")
            params.append(str(job_id))

        if user:
            conditions.append("user=?")
            params.append(str(user))

        if queue:
            conditions.append("queue=?")
            params.append(str(queue))

        if status:
            conditions.append("status=?")
            params.append(str(status))

        select_condition = ''

        if conditions:
            select_condition = 'WHERE ' + ' AND '.join(conditions)

        if limit:
            select_condition += f" LIMIT {int(limit)}"

        key_list = ['job', 'job_name', 'user', 'status', 'queue', 'started_time', 'finished_time', 'max_mem', 'avg_mem', 'rusage_mem', 'exit_code', 'command']
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', 'job', key_list=key_list, select_condition=select_condition, select_params=params)

        if not data_dic:
            return f"No matching jobs found for date {date}."

        num_rows = len(data_dic.get('job', []))
        lines = [f"Found {num_rows} job(s) for date {date}:\n"]

        for i in range(num_rows):
            row_parts = []

            for key in key_list:
                val = data_dic.get(key, [''])[i] if i < len(data_dic.get(key, [])) else ''
                row_parts.append(f"{key}={val}")

            lines.append('  '.join(row_parts))

        output = '\n'.join(lines)

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"

        return output
    except Exception as e:
        return f"Error querying job history: {e}"


# ============================================================
# Documentation loading and search (RAG vector + keyword fallback).
# ============================================================

def load_ai_documents(docs_dir):
    """
    Load documents from db/ai/rag/ directory.
    Prefers FAISS index (rag_faiss.index + rag_chunks.json).
    Falls back to keyword search if FAISS files are absent.
    Returns a dict: {"chunks": [...], "faiss_index": faiss.Index or None}
    """
    result = {"chunks": [], "faiss_index": None, "metadata": []}

    if not os.path.isdir(docs_dir):
        return result

    chunks_file = os.path.join(docs_dir, 'rag_chunks.json')
    faiss_file = os.path.join(docs_dir, 'rag_faiss.index')
    metadata_file = os.path.join(docs_dir, 'rag_metadata.json')

    # Try loading FAISS index.
    if os.path.exists(chunks_file) and os.path.exists(faiss_file) and FAISS_AVAILABLE:
        try:
            with open(chunks_file, 'r', errors='replace') as f:
                result["chunks"] = json.load(f)

            result["faiss_index"] = faiss.read_index(faiss_file)

            # Load metadata if available and length matches chunks.
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file, 'r', errors='replace') as f:
                        meta = json.load(f)

                    if len(meta) == len(result["chunks"]):
                        result["metadata"] = meta
                except Exception:
                    pass

            return result
        except Exception:
            pass

    # Fallback: load chunks for keyword search.
    if os.path.exists(chunks_file):
        try:
            with open(chunks_file, 'r', errors='replace') as f:
                result["chunks"] = json.load(f)

            # Load metadata if available and length matches chunks.
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file, 'r', errors='replace') as f:
                        meta = json.load(f)

                    if len(meta) == len(result["chunks"]):
                        result["metadata"] = meta
                except Exception:
                    pass
        except Exception:
            pass

    return result


def _get_query_embedding(query, api_base_url, api_key, embedding_model):
    """Get embedding vector for a search query via Ark multimodal embedding API."""
    try:
        import requests

        base_url = api_base_url.rstrip('/')
        url = base_url + '/embeddings/multimodal'
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        payload = {
            'model': embedding_model,
            'input': [{'type': 'text', 'text': query}]
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=15)

        if resp.status_code == 200:
            return np.array(resp.json()['data']['embedding'], dtype=np.float32)

        return None
    except Exception:
        return None


def execute_documentation_search(doc_data, query, api_base_url='', api_key='', embedding_model='', metadata=None):
    """
    Search documentation using FAISS vector search (preferred) or keyword fallback.
    doc_data: dict with "chunks" (list) and "faiss_index" (faiss.Index or None).
    Returns (result_text, matched_sources) where matched_sources is a list of metadata dicts.
    """
    chunks = doc_data.get("chunks", []) if isinstance(doc_data, dict) else doc_data
    faiss_index = doc_data.get("faiss_index", None) if isinstance(doc_data, dict) else None

    if metadata is None:
        metadata = []

    if not chunks:
        return ("No documentation loaded. Place RAG files (rag_chunks.json + rag_faiss.index) in the db/ai/rag/ directory.", [])

    if not query.strip():
        return ("Empty search query.", [])

    # Try FAISS vector search.
    if faiss_index is not None and api_base_url and api_key and embedding_model:
        query_vec = _get_query_embedding(query, api_base_url, api_key, embedding_model)

        if query_vec is not None:
            # Normalize query vector (index was built with normalized vectors).
            norm = np.linalg.norm(query_vec)

            if norm > 0:
                query_vec /= norm

            query_vec = query_vec.reshape(1, -1)
            scores, indices = faiss_index.search(query_vec, 15)
            results = []
            matched_sources = []
            total_len = 0

            for i in range(len(indices[0])):
                idx = indices[0][i]

                if idx < 0 or idx >= len(chunks):
                    continue

                if scores[0][i] < 0.3:
                    break

                chunk = chunks[idx]

                if total_len + len(chunk) > MAX_OUTPUT_LENGTH:
                    break

                results.append(chunk)
                total_len += len(chunk)

                if metadata and idx < len(metadata):
                    matched_sources.append(metadata[idx])
                else:
                    matched_sources.append({'source': f'RAG chunk #{idx + 1}'})

            if results:
                return ('\n\n---\n\n'.join(results), matched_sources)

    # Fallback: keyword search.
    keywords = query.lower().split()
    scored = []

    for i, chunk in enumerate(chunks):
        chunk_lower = chunk.lower()
        score = sum(1 for kw in keywords if kw in chunk_lower)

        if score > 0:
            scored.append((score, chunk, i))

    scored.sort(key=lambda x: -x[0])

    results = []
    matched_sources = []
    total_len = 0

    for score, chunk, chunk_idx in scored[:20]:
        if total_len + len(chunk) > MAX_OUTPUT_LENGTH:
            break

        results.append(chunk)
        total_len += len(chunk)

        if metadata and chunk_idx < len(metadata):
            matched_sources.append(metadata[chunk_idx])
        else:
            matched_sources.append({'source': f'RAG chunk #{chunk_idx + 1}'})

    if results:
        return ('\n\n---\n\n'.join(results), matched_sources)

    return (f"No documentation found for: {query}", [])


class DocLoaderThread(QThread):
    """Background thread for loading AI documents."""
    finished_signal = pyqtSignal(dict)

    def __init__(self, docs_dir):
        super().__init__()
        self.docs_dir = docs_dir

    def run(self):
        doc_data = load_ai_documents(self.docs_dir)
        self.finished_signal.emit(doc_data)


# ============================================================
# Skill loading (config/ai/*/SKILL.md).
# ============================================================

def load_skills(skills_dir):
    """
    Load skills from skills_dir. Each subdirectory with a SKILL.md is one skill.
    Returns a list of dicts: [{"name": ..., "tags": [...], "content": ...}, ...]
    """
    skills = []

    if not os.path.isdir(skills_dir):
        return skills

    for name in sorted(os.listdir(skills_dir)):
        skill_file = os.path.join(skills_dir, name, 'SKILL.md')

        if not os.path.isfile(skill_file):
            continue

        try:
            with open(skill_file, 'r', errors='replace') as f:
                text = f.read()
        except Exception:
            continue

        # Parse YAML frontmatter for tags.
        tags = []

        if text.startswith('---'):
            parts = text.split('---', 2)

            if len(parts) >= 3:
                for line in parts[1].splitlines():
                    line = line.strip().lstrip('- ').strip()

                    if line and not line.endswith(':') and ':' not in line:
                        tags.append(line.lower())

                text = parts[2].strip()

        skills.append({"name": name, "tags": tags, "content": text})

    return skills


def _tag_matches(tag, msg_lower):
    """Check if a skill tag matches the message. Word-boundary for ASCII tags, substring for CJK."""
    if tag.isascii():
        return bool(re.search(r'\b' + re.escape(tag) + r'\b', msg_lower))

    return tag in msg_lower


def match_skills(skills, user_message):
    """
    Check if user_message matches any skill tags.
    Returns (content_string, matched_skill_names).
    """
    if not skills:
        return ('', [])

    msg_lower = user_message.lower()
    matched_content = []
    matched_names = []

    for skill in skills:
        for tag in skill['tags']:
            if _tag_matches(tag, msg_lower):
                matched_content.append(skill['content'])
                matched_names.append(skill['name'])
                break

    return ('\n\n'.join(matched_content), matched_names)


# ============================================================
# Message format converters (OpenAI <-> Anthropic).
# ============================================================

def openai_messages_to_anthropic(messages):
    """
    Convert OpenAI-format messages to Anthropic format.
    Returns (system_prompt, anthropic_messages).
    """
    system = ""
    anthropic_msgs = []

    for msg in messages:
        role = msg.get('role', '')

        if role == 'system':
            system = msg.get('content', '')
        elif role == 'user':
            anthropic_msgs.append({"role": "user", "content": msg['content']})
        elif role == 'assistant':
            content_blocks = []

            if msg.get('content'):
                content_blocks.append({"type": "text", "text": msg['content']})

            for tc in msg.get('tool_calls', []):
                func = tc.get('function', {})

                try:
                    input_data = json.loads(func.get('arguments', '{}'))
                except json.JSONDecodeError:
                    input_data = {}

                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get('id', ''),
                    "name": func.get('name', ''),
                    "input": input_data
                })

            if content_blocks:
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
        elif role == 'tool':
            tool_result = {
                "type": "tool_result",
                "tool_use_id": msg.get('tool_call_id', ''),
                "content": msg.get('content', '')
            }

            # Anthropic expects tool results inside a user message.
            # Group consecutive tool results into one user message.
            if anthropic_msgs and anthropic_msgs[-1]['role'] == 'user' and isinstance(anthropic_msgs[-1]['content'], list):
                anthropic_msgs[-1]['content'].append(tool_result)
            else:
                anthropic_msgs.append({"role": "user", "content": [tool_result]})

    return system, anthropic_msgs


# ============================================================
# AiChatThread - supports both OpenAI and Anthropic APIs.
# ============================================================

class AiChatThread(QThread):
    """Worker thread for AI chat with streaming and tool calling."""
    token_received = pyqtSignal(str)
    tool_call_start = pyqtSignal(str, str)
    tool_call_result = pyqtSignal(str, str)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    confirm_requested = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    sources_signal = pyqtSignal(dict)

    def __init__(self, api_base_url, api_key, model_name, messages,
                 db_path='', license_dic=None, lmstat_path='lmstat', lmstat_bsub_command='',
                 dangerous_commands=None, doc_chunks=None, skills=None,
                 embedding_model='', embedding_api_base_url='', embedding_api_key='',
                 debug=False, confirm_mode='signal', max_tokens=None, max_loops=None):
        super().__init__()
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.messages = messages
        self.db_path = db_path
        self.license_dic = license_dic or {}
        self.lmstat_path = lmstat_path
        self.lmstat_bsub_command = lmstat_bsub_command
        self.dangerous_commands = dangerous_commands or DEFAULT_DANGEROUS_COMMANDS
        self.doc_chunks = doc_chunks or []
        self.skills = skills or []
        self.embedding_model = embedding_model
        self.embedding_api_base_url = embedding_api_base_url.rstrip('/') if embedding_api_base_url else self.api_base_url
        self.embedding_api_key = embedding_api_key if embedding_api_key else self.api_key
        self.debug = debug
        self.confirm_mode = confirm_mode
        self.max_tokens = max_tokens
        self.max_loops = max_loops
        self._stop_flag = False
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._sources = {"rag_sources": [], "skills": []}
        self._timing_stats = {"llm_total": 0.0, "llm_first_token_max": 0.0, "llm_generation_total": 0.0, "tool_total": 0.0, "llm_calls": 0, "output_tokens": 0}

        # Auto-detect API type.
        self.api_type = detect_api_type(model_name)

        # Copy the system message dict so skill injection doesn't
        # accumulate across conversations (messages list is shared by reference).
        if self.messages and self.messages[0].get('role') == 'system':
            self.messages[0] = dict(self.messages[0])

        # Track system prompt composition for debug output.
        self._prompt_parts = {}
        _base_len = len(self.messages[0].get('content', '')) if self.messages else 0
        self._prompt_parts['base'] = _base_len

        # Inject matched skill content into system prompt for this conversation.
        self._inject_skills()
        _after_skills = len(self.messages[0].get('content', '')) if self.messages else 0
        self._prompt_parts['skill'] = _after_skills - _base_len

        # Debug: log injection details.
        if self.debug:
            if self._sources.get("skills"):
                common.bprint(f'[AI Debug] Injected skills: {self._sources["skills"]}', date_format='%Y-%m-%d %H:%M:%S')
            else:
                common.bprint('[AI Debug] No skill matched, experience/harness disabled', date_format='%Y-%m-%d %H:%M:%S')

    def _inject_skills(self):
        """Check the latest user message against skill tags, inject matched skills into system prompt."""
        if not self.skills:
            return

        # Find the last user message.
        user_msg = ''

        for msg in reversed(self.messages):
            if msg.get('role') == 'user':
                user_msg = msg.get('content', '')
                break

        if not user_msg:
            return

        skill_content, skill_names = match_skills(self.skills, user_msg)

        if skill_names:
            self._sources["skills"] = skill_names

        if skill_content and self.messages and self.messages[0].get('role') == 'system':
            self.messages[0]['content'] = self.messages[0]['content'] + '\n\n' + skill_content

    def stop(self):
        self._stop_flag = True

    def set_confirm_result(self, result):
        """Called from main thread to respond to confirmation request."""
        self._confirm_result = result
        self._confirm_event.set()

    def _request_confirmation(self, command):
        """Request user confirmation for dangerous command. Blocks until user responds."""
        # Headless/read-only mode: reject any state-changing command without prompting.
        if self.confirm_mode == 'auto_reject':
            return False

        self._confirm_result = False
        self._confirm_event.clear()
        self.confirm_requested.emit(command)

        while not self._confirm_event.wait(timeout=1.0):
            if self._stop_flag:
                return False

        return self._confirm_result

    def run(self):
        try:
            if self.api_type == 'anthropic':
                self._agent_loop_anthropic()
            else:
                self._agent_loop_openai()
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            if self.debug:
                print('')

            self.sources_signal.emit(self._sources)
            self.finished_signal.emit()

    @staticmethod
    def _tool_description(tool_name, args):
        """Generate a human-readable description of the tool call."""
        if tool_name == 'run_command':
            return 'Executing: ' + args.get('command', '')
        elif tool_name == 'query_license_info':
            return 'Querying license info'
        elif tool_name == 'query_job_history':
            return 'Querying job history'
        elif tool_name == 'search_documentation':
            return 'Searching documentation: ' + args.get('query', '')

        return 'Calling ' + tool_name

    def _execute_tool(self, tool_name, args):
        if tool_name == 'run_command':
            return execute_command(
                args.get('command', ''),
                self.dangerous_commands,
                self._request_confirmation
            )
        elif tool_name == 'query_license_info':
            return execute_license_query(
                self.license_dic,
                lmstat_path=self.lmstat_path,
                bsub_command=self.lmstat_bsub_command,
                feature=args.get('feature', ''),
                user=args.get('user', ''),
                server=args.get('server', '')
            )
        elif tool_name == 'query_job_history':
            return execute_job_history_query(
                self.db_path,
                job_id=args.get('job_id', ''),
                user=args.get('user', ''),
                queue=args.get('queue', ''),
                status=args.get('status', ''),
                date=args.get('date', ''),
                limit=args.get('limit', 20)
            )
        elif tool_name == 'search_documentation':
            doc_metadata = self.doc_chunks.get("metadata", []) if isinstance(self.doc_chunks, dict) else []
            result_text, rag_sources = execute_documentation_search(
                self.doc_chunks,
                args.get('query', ''),
                api_base_url=self.embedding_api_base_url,
                api_key=self.embedding_api_key,
                embedding_model=self.embedding_model,
                metadata=doc_metadata
            )
            self._sources["rag_sources"].extend(rag_sources)
            return result_text

        return f"Unknown tool: {tool_name}"

    def _convert_tool_messages_for_fallback(self):
        """Convert tool-call messages to plain user/assistant format for APIs with incomplete tool support."""
        fallback = []

        for msg in self.messages:
            role = msg.get('role', '')

            if role == 'tool':
                tool_id = msg.get('tool_call_id', '')
                content = msg.get('content', '')
                fallback.append({"role": "user", "content": f"[Tool result ({tool_id})]:\n{content}"})
            elif role == 'assistant' and msg.get('tool_calls'):
                # Keep the text content, convert tool_calls to text description.
                parts = []
                text_content = msg.get('content') or ''

                if text_content:
                    parts.append(text_content)

                for tc in msg['tool_calls']:
                    fn = tc.get('function', {})
                    parts.append(f"[Calling tool: {fn.get('name', '')}({fn.get('arguments', '')})]")

                fallback.append({"role": "assistant", "content": '\n'.join(parts)})
            else:
                fallback.append(msg)

        return fallback

    # ==========================================================
    # OpenAI-compatible API loop (OpenAI, DeepSeek, Ark, vLLM).
    # ==========================================================

    # Class-level cache for SDK clients to avoid repeated import + init cost.
    _openai_client_cache = {}   # {(base_url, api_key): client}
    _anthropic_client_cache = {}

    def _get_openai_client(self):
        """Get or create a cached OpenAI client."""
        import time as _time
        _t_start = _time.time()

        from openai import OpenAI

        base_url = self.api_base_url

        if base_url.endswith('/chat/completions'):
            base_url = base_url[:-len('/chat/completions')]

        if not any(f'/v{n}' in base_url for n in range(1, 10)):
            base_url = base_url + '/v1'

        cache_key = (base_url, self.api_key)

        if cache_key not in AiChatThread._openai_client_cache:
            AiChatThread._openai_client_cache[cache_key] = OpenAI(base_url=base_url, api_key=self.api_key, timeout=120.0)

        if self.debug:
            common.bprint(f'[AI Debug] openai client ready: {_time.time() - _t_start:.2f}s', date_format='%Y-%m-%d %H:%M:%S')

        return AiChatThread._openai_client_cache[cache_key]

    def _get_anthropic_client(self):
        """Get or create a cached Anthropic client."""
        import anthropic

        cache_key = (self.api_base_url, self.api_key)

        if cache_key not in AiChatThread._anthropic_client_cache:
            AiChatThread._anthropic_client_cache[cache_key] = anthropic.Anthropic(base_url=self.api_base_url, api_key=self.api_key)

        return AiChatThread._anthropic_client_cache[cache_key]

    def _agent_loop_openai(self):
        import time as _time

        try:
            client = self._get_openai_client()
        except ImportError:
            self.error_signal.emit('openai package is not installed. Run: pip install openai')
            return

        max_loops = self.max_loops if self.max_loops else 10

        for loop_i in range(max_loops):
            if self._stop_flag:
                return

            if self.debug:
                _sys_len = len(self.messages[0].get('content', '')) if self.messages else 0
                _non_sys_chars = sum(len(str(m.get('content', ''))) for m in self.messages[1:])
                _total_chars = _sys_len + _non_sys_chars
                _msg_count = len(self.messages)
                _parts = self._prompt_parts
                _skill_info = f', skill={_parts["skill"]}' if _parts.get('skill', 0) > 0 else ''

                common.bprint(f'[AI Debug] ──── Loop {loop_i} ────', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(f'[AI Debug] INPUT: {_msg_count} msgs, system={_sys_len}(base={_parts.get("base", 0)}{_skill_info}), conversation={_non_sys_chars}, total={_total_chars} chars', date_format='%Y-%m-%d %H:%M:%S')

                # Only show message details in first loop; subsequent loops just add tool results.
                if loop_i == 0:
                    _trunc_len = 200

                    for _mi, _msg in enumerate(self.messages[1:], 1):
                        _role = _msg.get('role', '')
                        _full_content = str(_msg.get('content', ''))
                        _content_display = _full_content[:_trunc_len] + '...' if len(_full_content) > _trunc_len else _full_content
                        common.bprint(f'[AI Debug]   [{_mi}] {_role}: {_content_display}', date_format='%Y-%m-%d %H:%M:%S')
                else:
                    # Show the last few messages (tool results added since previous loop).
                    for _msg in self.messages[-3:]:
                        _role = _msg.get('role', '')

                        if _role in ('tool', 'user'):
                            _content = str(_msg.get('content', ''))[:150]
                            common.bprint(f'[AI Debug]   +{_role}: {_content}', date_format='%Y-%m-%d %H:%M:%S')

            self.status_signal.emit('Waiting for LLM response')
            _t_api = _time.time()

            # On the last iteration, omit tools and inject a summary instruction.
            is_last_loop = (loop_i == max_loops - 1)

            if is_last_loop:
                self.messages.append({"role": "user", "content": "请基于以上所有已收集的信息，给出完整的分析结论和可操作的建议。不要再请求更多数据。"})

            _api_kwargs = dict(
                model=self.model_name,
                messages=self.messages,
                temperature=0,
                stream=True,
                stream_options={"include_usage": True}
            )

            if not is_last_loop:
                _api_kwargs['tools'] = TOOLS_OPENAI

            if self.max_tokens:
                _api_kwargs['max_tokens'] = self.max_tokens

            try:
                response = client.chat.completions.create(**_api_kwargs)
            except Exception as e:
                # Retry once without tools parameter in case API rejects tool messages.
                if self.debug:
                    common.bprint(f'[AI Debug] API call failed: {e}, retrying without tools ...', date_format='%Y-%m-%d %H:%M:%S')

                try:
                    # Convert tool messages to user messages for compatibility.
                    fallback_messages = self._convert_tool_messages_for_fallback()

                    _fallback_kwargs = dict(
                        model=self.model_name,
                        messages=fallback_messages,
                        temperature=0,
                        stream=True,
                        stream_options={"include_usage": True}
                    )

                    if self.max_tokens:
                        _fallback_kwargs['max_tokens'] = self.max_tokens

                    response = client.chat.completions.create(**_fallback_kwargs)
                except Exception as e2:
                    self.error_signal.emit(f"API call failed: {e2}")
                    return

            full_content = ""
            tool_calls_data = {}
            _first_chunk = True
            _first_token_time = 0.0
            _first_token_abs = 0.0
            _completion_tokens = 0
            _chunk_count = 0

            try:
                for chunk in response:
                    if _first_chunk:
                        _first_token_abs = _time.time()
                        _first_token_time = _first_token_abs - _t_api
                        self._timing_stats["llm_first_token_max"] = max(self._timing_stats["llm_first_token_max"], _first_token_time)
                        _first_chunk = False

                    if self._stop_flag:
                        return

                    # Capture usage from the final chunk (requires stream_options={"include_usage": True}).
                    if hasattr(chunk, 'usage') and chunk.usage and hasattr(chunk.usage, 'completion_tokens'):
                        _completion_tokens = chunk.usage.completion_tokens

                    choice = chunk.choices[0] if chunk.choices else None

                    if not choice:
                        continue

                    delta = choice.delta

                    if delta and delta.content:
                        full_content += delta.content
                        _chunk_count += 1

                        # On last loop (no tools), stream text immediately.
                        # Otherwise buffer until we know if tool calls follow.
                        if is_last_loop:
                            self.token_received.emit(delta.content)

                    if delta and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index

                            if idx not in tool_calls_data:
                                tool_calls_data[idx] = {'id': '', 'name': '', 'arguments': ''}

                            if tc.id:
                                tool_calls_data[idx]['id'] = tc.id

                            if tc.function and tc.function.name:
                                tool_calls_data[idx]['name'] = tc.function.name

                            if tc.function and tc.function.arguments:
                                tool_calls_data[idx]['arguments'] += tc.function.arguments
                                _chunk_count += 1
            except Exception as e:
                self.error_signal.emit(f"Stream error: {e}")
                return

            # Prefer API-reported token count; fall back to chunk count for private deployments.
            self._timing_stats["output_tokens"] += _completion_tokens if _completion_tokens > 0 else _chunk_count

            _t_end = _time.time()
            _llm_elapsed = _t_end - _t_api
            _generation_time = (_t_end - _first_token_abs) if _first_token_abs > 0 else 0
            self._timing_stats["llm_total"] += _llm_elapsed
            self._timing_stats["llm_calls"] += 1
            self._timing_stats["llm_generation_total"] += _generation_time

            _effective_tokens = _completion_tokens if _completion_tokens > 0 else _chunk_count
            _tpm = (_generation_time / _effective_tokens * 1000) if _effective_tokens > 0 else 0
            _token_source = 'api' if _completion_tokens > 0 else 'chunk'

            if self.debug:
                _tool_names = [tool_calls_data[i]['name'] for i in sorted(tool_calls_data.keys())] if tool_calls_data else []
                _output_summary = f'content={len(full_content)} chars' if full_content else 'no content'

                if _tool_names:
                    _output_summary += f', tools=[{", ".join(_tool_names)}]'

                common.bprint(f'[AI Debug] OUTPUT: {_output_summary}', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(f'[AI Debug] PERF: total={_llm_elapsed:.1f}s, first_token={_first_token_time:.2f}s, tokens={_effective_tokens}({_token_source}), TPM={_tpm:.0f}ms/token', date_format='%Y-%m-%d %H:%M:%S')

            if not tool_calls_data:
                # Fallback: check if model output tool calls as XML text.
                xml_tool_calls = parse_xml_tool_calls(full_content) if full_content else []

                if not xml_tool_calls:
                    if full_content:
                        # Emit buffered text (already streamed on last loop).
                        if not is_last_loop:
                            self.token_received.emit(full_content)

                        self.messages.append({"role": "assistant", "content": full_content})
                    else:
                        self.error_signal.emit('LLM returned empty response, please retry.')

                    return

                if self.debug:
                    common.bprint(f'[AI Debug] Parsed {len(xml_tool_calls)} tool call(s) from XML in content (fallback)', date_format='%Y-%m-%d %H:%M:%S')

                # Strip XML block from displayed content.
                display_content = re.sub(r'<function_calls>.*?</function_calls>', '', full_content, flags=re.DOTALL).strip()

                if display_content:
                    self.messages.append({"role": "assistant", "content": display_content})

                for i, xtc in enumerate(xml_tool_calls):
                    if self._stop_flag:
                        return

                    tool_name = xtc['name']

                    try:
                        args = json.loads(xtc['arguments'])
                    except json.JSONDecodeError:
                        args = {}

                    self.tool_call_start.emit(tool_name, self._tool_description(tool_name, args))
                    _t_tool = _time.time()
                    result = self._execute_tool(tool_name, args)
                    self._timing_stats["tool_total"] += _time.time() - _t_tool

                    if self.debug:
                        common.bprint(f'[AI Debug] Tool "{tool_name}" executed (xml fallback): {_time.time() - _t_tool:.2f}s, result={len(result)} chars', date_format='%Y-%m-%d %H:%M:%S')

                    self.tool_call_result.emit(tool_name, result)

                    # Append as user message with tool result so model can continue.
                    self.messages.append({"role": "user", "content": f"[Tool result from {tool_name}]:\n{result}"})

                continue

            assistant_tool_calls = []

            for idx in sorted(tool_calls_data.keys()):
                tc = tool_calls_data[idx]
                assistant_tool_calls.append({
                    "id": tc['id'],
                    "type": "function",
                    "function": {"name": tc['name'], "arguments": tc['arguments']}
                })

            self.messages.append({
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": assistant_tool_calls
            })

            for idx in sorted(tool_calls_data.keys()):
                if self._stop_flag:
                    return

                tc = tool_calls_data[idx]
                tool_name = tc['name']

                try:
                    args = json.loads(tc['arguments'])
                except json.JSONDecodeError:
                    args = {}

                self.tool_call_start.emit(tool_name, self._tool_description(tool_name, args))
                _t_tool = _time.time()
                result = self._execute_tool(tool_name, args)
                self._timing_stats["tool_total"] += _time.time() - _t_tool

                if self.debug:
                    common.bprint(f'[AI Debug] Tool "{tool_name}" executed: {_time.time() - _t_tool:.2f}s, result={len(result)} chars', date_format='%Y-%m-%d %H:%M:%S')

                self.tool_call_result.emit(tool_name, result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc['id'],
                    "content": result
                })

    # ==========================================================
    # Anthropic API loop (Claude models via anthropic SDK).
    # ==========================================================

    def _agent_loop_anthropic(self):
        import time as _time

        try:
            _t_start = _time.time()
            client = self._get_anthropic_client()

            if self.debug:
                common.bprint(f'[AI Debug] anthropic client ready: {_time.time() - _t_start:.2f}s', date_format='%Y-%m-%d %H:%M:%S')
        except ImportError:
            self.error_signal.emit('anthropic package is not installed. Run: pip install anthropic')
            return

        max_loops = self.max_loops if self.max_loops else 10

        for loop_i in range(max_loops):
            if self._stop_flag:
                return

            # Debug: log prompt size breakdown.
            if self.debug:
                _sys_len = len(self.messages[0].get('content', '')) if self.messages else 0
                _non_sys_chars = sum(len(str(m.get('content', ''))) for m in self.messages[1:])
                _total_chars = _sys_len + _non_sys_chars
                _msg_count = len(self.messages)
                _parts = self._prompt_parts
                _skill_info = f', skill={_parts["skill"]}' if _parts.get('skill', 0) > 0 else ''

                common.bprint(f'[AI Debug] ──── Loop {loop_i} ────', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(f'[AI Debug] INPUT: {_msg_count} msgs, system={_sys_len}(base={_parts.get("base", 0)}{_skill_info}), conversation={_non_sys_chars}, total={_total_chars} chars', date_format='%Y-%m-%d %H:%M:%S')

            self.status_signal.emit('Waiting for LLM response')

            # On the last iteration, omit tools and inject a summary instruction.
            is_last_loop = (loop_i == max_loops - 1)

            if is_last_loop:
                self.messages.append({"role": "user", "content": "请基于以上所有已收集的信息，给出完整的分析结论和可操作的建议。不要再请求更多数据。"})

            # Convert messages to Anthropic format.
            system, anthropic_msgs = openai_messages_to_anthropic(self.messages)

            _t_api = _time.time()

            _api_kwargs = dict(
                model=self.model_name,
                system=system,
                messages=anthropic_msgs,
                max_tokens=(self.max_tokens or 4096),
                temperature=0,
                stream=True
            )

            if not is_last_loop:
                _api_kwargs['tools'] = TOOLS_ANTHROPIC

            try:
                stream = client.messages.create(**_api_kwargs)
            except Exception as e:
                self.error_signal.emit(f"API call failed: {e}")
                return

            full_content = ""
            tool_calls = {}  # {block_index: {id, name, arguments}}
            _first_chunk = True
            _first_token_time = 0.0
            _first_token_abs = 0.0
            _completion_tokens = 0
            _chunk_count = 0

            try:
                for event in stream:
                    if self._stop_flag:
                        return

                    if _first_chunk:
                        _first_token_abs = _time.time()
                        _first_token_time = _first_token_abs - _t_api
                        self._timing_stats["llm_first_token_max"] = max(self._timing_stats["llm_first_token_max"], _first_token_time)
                        _first_chunk = False

                    # Capture output_tokens from message_delta event (Anthropic usage).
                    if event.type == 'message_delta' and hasattr(event, 'usage'):
                        _completion_tokens = getattr(event.usage, 'output_tokens', 0)

                    if event.type == 'content_block_start':
                        if event.content_block.type == 'tool_use':
                            tool_calls[event.index] = {
                                'id': event.content_block.id,
                                'name': event.content_block.name,
                                'arguments': ''
                            }
                    elif event.type == 'content_block_delta':
                        if event.delta.type == 'text_delta':
                            full_content += event.delta.text
                            _chunk_count += 1

                            if is_last_loop:
                                self.token_received.emit(event.delta.text)
                        elif event.delta.type == 'input_json_delta':
                            if event.index in tool_calls:
                                tool_calls[event.index]['arguments'] += event.delta.partial_json
                                _chunk_count += 1
            except Exception as e:
                self.error_signal.emit(f"Stream error: {e}")
                return

            # Prefer API-reported token count; fall back to chunk count for private deployments.
            self._timing_stats["output_tokens"] += _completion_tokens if _completion_tokens > 0 else _chunk_count

            _t_end = _time.time()
            _llm_elapsed = _t_end - _t_api
            _generation_time = (_t_end - _first_token_abs) if _first_token_abs > 0 else 0
            self._timing_stats["llm_total"] += _llm_elapsed
            self._timing_stats["llm_calls"] += 1
            self._timing_stats["llm_generation_total"] += _generation_time

            _effective_tokens = _completion_tokens if _completion_tokens > 0 else _chunk_count
            _tpm = (_generation_time / _effective_tokens * 1000) if _effective_tokens > 0 else 0
            _token_source = 'api' if _completion_tokens > 0 else 'chunk'

            if self.debug:
                _tool_names = [tool_calls[i]['name'] for i in sorted(tool_calls.keys())] if tool_calls else []
                _output_summary = f'content={len(full_content)} chars' if full_content else 'no content'

                if _tool_names:
                    _output_summary += f', tools=[{", ".join(_tool_names)}]'

                common.bprint(f'[AI Debug] OUTPUT: {_output_summary}', date_format='%Y-%m-%d %H:%M:%S')
                common.bprint(f'[AI Debug] PERF: total={_llm_elapsed:.1f}s, first_token={_first_token_time:.2f}s, tokens={_effective_tokens}({_token_source}), TPM={_tpm:.0f}ms/token', date_format='%Y-%m-%d %H:%M:%S')

            # No tool calls -> done (or fallback to XML parsing).
            if not tool_calls:
                xml_tool_calls = parse_xml_tool_calls(full_content) if full_content else []

                if not xml_tool_calls:
                    if full_content:
                        if not is_last_loop:
                            self.token_received.emit(full_content)

                        self.messages.append({"role": "assistant", "content": full_content})
                    else:
                        self.error_signal.emit('LLM returned empty response, please retry.')

                    return

                if self.debug:
                    common.bprint(f'[AI Debug] Parsed {len(xml_tool_calls)} tool call(s) from XML in content (fallback)', date_format='%Y-%m-%d %H:%M:%S')

                display_content = re.sub(r'<function_calls>.*?</function_calls>', '', full_content, flags=re.DOTALL).strip()

                if display_content:
                    self.messages.append({"role": "assistant", "content": display_content})

                for i, xtc in enumerate(xml_tool_calls):
                    if self._stop_flag:
                        return

                    tool_name = xtc['name']

                    try:
                        args = json.loads(xtc['arguments'])
                    except json.JSONDecodeError:
                        args = {}

                    self.tool_call_start.emit(tool_name, self._tool_description(tool_name, args))
                    _t_tool = _time.time()
                    result = self._execute_tool(tool_name, args)
                    self._timing_stats["tool_total"] += _time.time() - _t_tool

                    if self.debug:
                        common.bprint(f'[AI Debug] Tool "{tool_name}" executed (xml fallback): {_time.time() - _t_tool:.2f}s, result={len(result)} chars', date_format='%Y-%m-%d %H:%M:%S')

                    self.tool_call_result.emit(tool_name, result)
                    self.messages.append({"role": "user", "content": f"[Tool result from {tool_name}]:\n{result}"})

                continue

            # Build assistant message in OpenAI format (for message history).
            assistant_tool_calls = []

            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                assistant_tool_calls.append({
                    "id": tc['id'],
                    "type": "function",
                    "function": {"name": tc['name'], "arguments": tc['arguments']}
                })

            self.messages.append({
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": assistant_tool_calls
            })

            # Execute each tool call.
            for idx in sorted(tool_calls.keys()):
                if self._stop_flag:
                    return

                tc = tool_calls[idx]
                tool_name = tc['name']

                try:
                    args = json.loads(tc['arguments'])
                except json.JSONDecodeError:
                    args = {}

                self.tool_call_start.emit(tool_name, self._tool_description(tool_name, args))
                _t_tool = _time.time()
                result = self._execute_tool(tool_name, args)
                self._timing_stats["tool_total"] += _time.time() - _t_tool

                if self.debug:
                    common.bprint(f'[AI Debug] Tool "{tool_name}" executed: {_time.time() - _t_tool:.2f}s, result={len(result)} chars', date_format='%Y-%m-%d %H:%M:%S')

                self.tool_call_result.emit(tool_name, result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc['id'],
                    "content": result
                })


# ============================================================
# Cluster analyze report (shared by bsample headless + bmonitor GUI).
# ============================================================


def _collect_raw_data(tool='lsf'):
    """
    Parallel collection of all LSF data needed by compute_cluster_metrics and
    collect_cluster_snapshot. Eliminates redundant serial command execution.
    Returns a dict with all raw results.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from common import common_lsf

    results = {}

    def _safe_call(key, func, *args, **kwargs):
        try:
            return key, func(*args, **kwargs)
        except Exception as e:
            return key, e

    def _safe_command(key, command, max_lines=120):
        try:
            returncode, stdout, stderr = common.run_command(command)
            text = (stdout.decode('utf-8', 'ignore') + stderr.decode('utf-8', 'ignore')).strip()

            if not text:
                return key, '(no output)'

            lines = text.splitlines()

            if len(lines) > max_lines:
                lines = lines[:max_lines] + [f'... ({len(lines) - max_lines} more lines omitted)']

            return key, '\n'.join(lines)
        except Exception as e:
            return key, f'(failed: {e})'

    lsload_kwargs = {'command': 'lsload -l'} if tool == 'openlava' else {}

    tasks = [
        ('lsid', common_lsf.get_lsid_info),
        ('bhosts', common_lsf.get_bhosts_info),
        ('bqueues', common_lsf.get_bqueues_info),
        ('lsload', lambda: common_lsf.get_lsload_info(**lsload_kwargs)),
        ('lshosts', common_lsf.get_lshosts_info),
        ('busers', common_lsf.get_busers_info),
        ('queue_host', common_lsf.get_queue_host_info),
    ]

    command_tasks = [
        ('bjobs_raw', 'bjobs -u all -w'),
        ('bjobs_pend_raw', 'bjobs -u all -p'),
        ('bqueues_l_raw', 'bqueues -l'),
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []

        for key, func in tasks:
            futures.append(executor.submit(_safe_call, key, func))

        for key, command in command_tasks:
            futures.append(executor.submit(_safe_command, key, command))

        for future in as_completed(futures):
            key, value = future.result()
            results[key] = value

    return results


def _render_dict_table(data_dic, max_rows=60):
    """Render a {column: [values]} dict (from common_lsf getters) as a compact text table."""
    if not data_dic:
        return '(no data)'

    keys = list(data_dic.keys())
    num_rows = max((len(v) for v in data_dic.values()), default=0)
    lines = ['\t'.join(keys)]

    for i in range(min(num_rows, max_rows)):
        row = [str(data_dic[k][i]) if i < len(data_dic[k]) else '' for k in keys]
        lines.append('\t'.join(row))

    if num_rows > max_rows:
        lines.append(f'... ({num_rows - max_rows} more rows omitted)')

    return '\n'.join(lines)


def _render_command_output(command, max_lines=120, timeout=None):
    """Run a read-only command and return its (line-capped) raw text output."""
    try:
        returncode, stdout, stderr = common.run_command(command, timeout=timeout)
        text = (stdout.decode('utf-8', 'ignore') + stderr.decode('utf-8', 'ignore')).strip()
    except Exception as error:
        return f'(failed: {error})'

    if not text:
        return '(no output)'

    lines = text.splitlines()

    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f'... ({len(lines) - max_lines} more lines omitted)']

    return '\n'.join(lines)


def collect_cluster_snapshot(tool='lsf', raw_data=None):
    """
    Collect a high-level, read-only cluster snapshot for the analyze report.
    Covers queues / hosts / load / lshosts / busers / jobs / pending-reasons /
    queue-config (no license). Each section is capped. Returns a plain-text block.

    Jobs / pending-reasons / queue-config are pre-collected here (rather than left
    for the agent to drill into) so the report can be produced in 1-2 LLM round trips
    instead of 6-7 — ~60% fewer tokens and ~half the wall time, with no quality loss.

    If raw_data is provided (from _collect_raw_data), uses it directly to avoid
    redundant command execution.
    """
    from common import common_lsf

    sections = []

    if raw_data:
        # Use pre-collected data.
        lsid = raw_data.get('lsid')

        if isinstance(lsid, tuple):
            tool_name, tool_version, cluster, master = lsid
            sections.append(f"[Cluster] tool={tool_name} version={tool_version} cluster={cluster} master={master}")
        elif isinstance(lsid, Exception):
            sections.append(f"[Cluster] (failed to get lsid info: {lsid})")
        else:
            sections.append("[Cluster] (no data)")

        dict_items = [
            ('Queues (bqueues -w)', 'bqueues'),
            ('Hosts (bhosts -w)', 'bhosts'),
            ('Load (lsload)', 'lsload'),
            ('Host config (lshosts -w)', 'lshosts'),
            ('Users (busers all)', 'busers'),
        ]

        for title, key in dict_items:
            data = raw_data.get(key)

            if isinstance(data, Exception):
                sections.append(f"[{title}] (failed: {data})")
            elif data:
                sections.append(f"[{title}]\n{_render_dict_table(data)}")
            else:
                sections.append(f"[{title}] (no data)")

        command_items = [
            ('Jobs (bjobs -u all -w)', 'bjobs_raw'),
            ('Pending reasons (bjobs -u all -p)', 'bjobs_pend_raw'),
            ('Queue config (bqueues -l)', 'bqueues_l_raw'),
        ]

        for title, key in command_items:
            output = raw_data.get(key, '(no data)')

            if isinstance(output, Exception):
                sections.append(f"[{title}] (failed: {output})")
            else:
                sections.append(f"[{title}]\n{output}")
    else:
        # Original serial collection (backward compatible).
        try:
            tool_name, tool_version, cluster, master = common_lsf.get_lsid_info()
            sections.append(f"[Cluster] tool={tool_name} version={tool_version} cluster={cluster} master={master}")
        except Exception as error:
            sections.append(f"[Cluster] (failed to get lsid info: {error})")

        collectors = [
            ('Queues (bqueues -w)', common_lsf.get_bqueues_info, {}),
            ('Hosts (bhosts -w)', common_lsf.get_bhosts_info, {}),
            ('Load (lsload)', common_lsf.get_lsload_info, ({'command': 'lsload -l'} if tool == 'openlava' else {})),
            ('Host config (lshosts -w)', common_lsf.get_lshosts_info, {}),
            ('Users (busers all)', common_lsf.get_busers_info, {}),
        ]

        for title, func, kwargs in collectors:
            try:
                data_dic = func(**kwargs)
                sections.append(f"[{title}]\n{_render_dict_table(data_dic)}")
            except Exception as error:
                sections.append(f"[{title}] (failed: {error})")

        raw_commands = [
            ('Jobs (bjobs -u all -w)', 'bjobs -u all -w'),
            ('Pending reasons (bjobs -u all -p)', 'bjobs -u all -p'),
            ('Queue config (bqueues -l)', 'bqueues -l'),
        ]

        for title, command in raw_commands:
            sections.append(f"[{title}]\n{_render_command_output(command)}")

    return '\n\n'.join(sections)


def _lsf_int(value):
    """Parse an LSF integer cell; return None for '-', 'unlimited', or non-numeric."""
    if value is not None and re.match(r'^\d+$', str(value).strip()):
        return int(value)

    return None


def _lsf_size_to_mb(value):
    """Convert an LSF size cell (e.g. '1.9G', '683_g', '512M', '2T') to MB; None if unparseable."""
    if value is None:
        return None

    match = re.match(r'^(\d+(?:\.\d+)?)[_]?([KMGTPkmgtp])', str(value).strip())

    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2).upper()
    factor = {'K': 1 / 1024, 'M': 1, 'G': 1024, 'T': 1024 * 1024, 'P': 1024 * 1024 * 1024}.get(unit, 1)

    return number * factor


def compute_cluster_metrics(tool='lsf', raw_data=None):
    """
    Compute EXACT, deterministic cluster aggregates for the analyze report's data
    sections (so they never depend on the LLM counting a truncated snapshot).

    If raw_data is provided (from _collect_raw_data), uses it directly to avoid
    redundant command execution.

    Reuses the slot/cpu/mem utilization algorithm proven in
    bsample.py:sample_utilization_info. Every aggregate degrades to None ('N/A')
    on missing data instead of raising.
    """
    from common import common_lsf

    metrics = {
        'cluster': '', 'master': '', 'tool': '', 'tool_version': '',
        'host_total': 0,
        'host_states': {'open': 0, 'closed_Admin': 0, 'closed_Busy': 0, 'closed_Full': 0, 'Others': 0},
        'slots_total': None,
        'slots_used': None,
        'slots_idle': None,
        'cores_total': None,
        'mem_total_gb': None,
        'mem_used_gb': None,
        'mem_idle_gb': None,
        'jobs_run': None,
        'jobs_pend': None,
        'jobs_susp': None,
        'util_slot': None,
        'util_cpu': None,
        'util_mem': None,
        'queues': [],
        'pending_reasons': [],
        'active_users': [],
        'hosts': [],
    }

    # ---- Cluster identity (lsid) ----
    if raw_data and 'lsid' in raw_data and isinstance(raw_data['lsid'], tuple):
        t_name, t_ver, cluster, master = raw_data['lsid']
        metrics['tool'] = t_name
        metrics['tool_version'] = t_ver
        metrics['cluster'] = cluster
        metrics['master'] = master
    else:
        try:
            t_name, t_ver, cluster, master = common_lsf.get_lsid_info()
            metrics['tool'] = t_name
            metrics['tool_version'] = t_ver
            metrics['cluster'] = cluster
            metrics['master'] = master
        except Exception:
            pass

    # ---- Hosts: state counts + slot totals (bhosts -w) ----
    if raw_data and 'bhosts' in raw_data and not isinstance(raw_data['bhosts'], Exception):
        bhosts_dic = raw_data['bhosts']
    else:
        try:
            bhosts_dic = common_lsf.get_bhosts_info()
        except Exception:
            bhosts_dic = {}

    host_names = bhosts_dic.get('HOST_NAME', [])
    metrics['host_total'] = len(host_names)
    status_list = bhosts_dic.get('STATUS', [])
    max_list = bhosts_dic.get('MAX', [])
    njobs_list = bhosts_dic.get('NJOBS', [])

    # Per-host slot capacity (bhosts MAX), used to derive per-queue slot totals.
    host_max_map = {}

    for idx, hname in enumerate(host_names):
        if idx < len(max_list):
            mv = _lsf_int(max_list[idx])

            if mv is not None:
                host_max_map[hname] = mv

    for status in status_list:
        status = (status or '').strip()

        if status == 'ok':
            metrics['host_states']['open'] += 1
        elif status.startswith('closed_Adm'):
            metrics['host_states']['closed_Admin'] += 1
        elif status.startswith('closed_Busy'):
            metrics['host_states']['closed_Busy'] += 1
        elif status.startswith('closed_Full'):
            metrics['host_states']['closed_Full'] += 1
        else:
            metrics['host_states']['Others'] += 1

    slots_total = sum(v for v in (_lsf_int(m) for m in max_list) if v is not None)
    slots_used = sum(v for v in (_lsf_int(n) for n in njobs_list) if v is not None)

    if max_list:
        metrics['slots_total'] = slots_total
        metrics['slots_used'] = slots_used
        metrics['slots_idle'] = slots_total - slots_used

        if slots_total > 0:
            metrics['util_slot'] = round(slots_used * 100 / slots_total, 1)

    # Suspended jobs across hosts (SSUSP + USUSP).
    susp = sum(v for v in (_lsf_int(s) for s in bhosts_dic.get('SSUSP', [])) if v is not None) \
        + sum(v for v in (_lsf_int(s) for s in bhosts_dic.get('USUSP', [])) if v is not None)

    if bhosts_dic.get('SSUSP') or bhosts_dic.get('USUSP'):
        metrics['jobs_susp'] = susp

    # ---- Queues: per-queue rows + cluster run/pend totals (bqueues -w) ----
    if raw_data and 'bqueues' in raw_data and not isinstance(raw_data['bqueues'], Exception):
        bqueues_dic = raw_data['bqueues']
    else:
        try:
            bqueues_dic = common_lsf.get_bqueues_info()
        except Exception:
            bqueues_dic = {}

    queue_names = bqueues_dic.get('QUEUE_NAME', [])

    # Per-queue total slots = sum of member-host MAX slots (same as the SLOTS
    # column on bmonitor's QUEUES page). bqueues' own MAX field is a per-queue
    # slot *limit* and is usually '-' (unlimited), so it is not what we want.
    if raw_data and 'queue_host' in raw_data and not isinstance(raw_data['queue_host'], Exception):
        queue_host_dic = raw_data['queue_host']
    else:
        try:
            queue_host_dic = common_lsf.get_queue_host_info()
        except Exception:
            queue_host_dic = {}

    queue_slots_map = {}

    for q_name, q_hosts in queue_host_dic.items():
        queue_slots_map[q_name] = sum(host_max_map[h] for h in q_hosts if h in host_max_map)

    if queue_names:
        run_total = 0
        pend_total = 0

        for i, name in enumerate(queue_names):
            def cell(key, _i=i):
                values = bqueues_dic.get(key, [])
                return values[_i] if _i < len(values) else ''

            njobs = _lsf_int(cell('NJOBS')) or 0
            pend = _lsf_int(cell('PEND')) or 0
            run = _lsf_int(cell('RUN')) or 0
            susp = _lsf_int(cell('SUSP')) or 0
            run_total += run
            pend_total += pend
            pend_rate = round(pend * 100 / njobs, 1) if njobs > 0 else 0.0

            metrics['queues'].append({
                'name': name,
                'prio': cell('PRIO'),
                'status': cell('STATUS'),
                'slots': queue_slots_map.get(name, cell('MAX')),
                'njobs': njobs,
                'pend': pend,
                'run': run,
                'susp': susp,
                'pend_rate': pend_rate,
            })

        metrics['jobs_run'] = run_total
        metrics['jobs_pend'] = pend_total

    # ---- CPU utilization: average lsload 'ut' across hosts ----
    if raw_data and 'lsload' in raw_data and not isinstance(raw_data['lsload'], Exception):
        lsload_dic = raw_data['lsload']
    else:
        try:
            lsload_dic = common_lsf.get_lsload_info(command='lsload -l') if tool == 'openlava' else common_lsf.get_lsload_info(command='lsload')
        except Exception:
            lsload_dic = {}

    ut_values = []

    for ut in lsload_dic.get('ut', []):
        if re.match(r'^\d+%$', str(ut).strip()):
            ut_values.append(int(str(ut).strip().rstrip('%')))

    if ut_values:
        metrics['util_cpu'] = round(sum(ut_values) / len(ut_values), 1)

    # ---- Memory utilization: aggregate (sum(maxmem-mem) / sum(maxmem)) ----
    if raw_data and 'lshosts' in raw_data and not isinstance(raw_data['lshosts'], Exception):
        lshosts_dic = raw_data['lshosts']
    else:
        try:
            lshosts_dic = common_lsf.get_lshosts_info()
        except Exception:
            lshosts_dic = {}

    # Total physical cores (lshosts ncpus).
    cores_total = sum(v for v in (_lsf_int(c) for c in lshosts_dic.get('ncpus', [])) if v is not None)

    if lshosts_dic.get('ncpus'):
        metrics['cores_total'] = cores_total

    maxmem_map = {}

    for i, host in enumerate(lshosts_dic.get('HOST_NAME', [])):
        maxmem_values = lshosts_dic.get('maxmem', [])

        if i < len(maxmem_values):
            maxmem_mb = _lsf_size_to_mb(maxmem_values[i])

            if maxmem_mb is not None:
                maxmem_map[host] = maxmem_mb

    total_max_mb = 0.0
    total_used_mb = 0.0

    for i, host in enumerate(lsload_dic.get('HOST_NAME', [])):
        mem_values = lsload_dic.get('mem', [])

        if host in maxmem_map and i < len(mem_values):
            free_mb = _lsf_size_to_mb(mem_values[i])
            maxmem_mb = maxmem_map[host]

            if free_mb is not None and maxmem_mb > 0:
                total_max_mb += maxmem_mb
                total_used_mb += max(maxmem_mb - free_mb, 0)

    if total_max_mb > 0:
        metrics['util_mem'] = round(total_used_mb * 100 / total_max_mb, 1)
        metrics['mem_total_gb'] = round(total_max_mb / 1024, 1)
        metrics['mem_used_gb'] = round(total_used_mb / 1024, 1)
        metrics['mem_idle_gb'] = round((total_max_mb - total_used_mb) / 1024, 1)

    # ---- Per-host detail: cpu% (lsload ut) and mem% by host ----
    ut_map = {}

    for i, host in enumerate(lsload_dic.get('HOST_NAME', [])):
        ut_values = lsload_dic.get('ut', [])

        if i < len(ut_values) and re.match(r'^\d+%$', str(ut_values[i]).strip()):
            ut_map[host] = int(str(ut_values[i]).strip().rstrip('%'))

    mempct_map = {}

    for i, host in enumerate(lsload_dic.get('HOST_NAME', [])):
        mem_values = lsload_dic.get('mem', [])

        if host in maxmem_map and i < len(mem_values):
            free_mb = _lsf_size_to_mb(mem_values[i])
            maxmem_mb = maxmem_map[host]

            if free_mb is not None and maxmem_mb > 0:
                mempct_map[host] = round(max(maxmem_mb - free_mb, 0) * 100 / maxmem_mb, 1)

    for i, host in enumerate(host_names):
        metrics['hosts'].append({
            'name': host,
            'status': (status_list[i] if i < len(status_list) else '').strip(),
            'max': max_list[i] if i < len(max_list) else '',
            'njobs': njobs_list[i] if i < len(njobs_list) else '',
            'ut': ut_map.get(host),
            'mem': mempct_map.get(host),
        })

    # ---- Pending-reason aggregation (bjobs -u all -p) ----
    try:
        metrics['pending_reasons'] = _aggregate_pending_reasons()
    except Exception:
        metrics['pending_reasons'] = []

    # ---- Active users (busers all): top by running + pending ----
    if raw_data and 'busers' in raw_data and not isinstance(raw_data['busers'], Exception):
        busers_dic = raw_data['busers']
    else:
        try:
            busers_dic = common_lsf.get_busers_info()
        except Exception:
            busers_dic = {}

    try:
        users = []

        for i, user in enumerate(busers_dic.get('USER/GROUP', [])):
            # Skip groups: real users contain a dot (e.g. "liyanqing.1987"),
            if user == 'default' or '.' not in user:
                continue

            run = _lsf_int(busers_dic.get('RUN', [''] * (i + 1))[i]) or 0
            pend = _lsf_int(busers_dic.get('PEND', [''] * (i + 1))[i]) or 0
            njobs = _lsf_int(busers_dic.get('NJOBS', [''] * (i + 1))[i]) or 0

            if njobs > 0 or run > 0 or pend > 0:
                users.append({'user': user, 'run': run, 'pend': pend, 'njobs': njobs})

        users.sort(key=lambda u: (u['run'], u['pend'], u['njobs']), reverse=True)
        metrics['active_users'] = users[:8]
    except Exception:
        metrics['active_users'] = []

    return metrics


def _aggregate_pending_reasons(command='bjobs -u all -p', top_n=6):
    """
    Parse 'bjobs -u all -p' and tally pending reasons across all pending jobs.
    Each job's indented reason lines are normalized (host counts stripped) and
    counted once per job. Returns [{'reason', 'count'}] sorted desc, top_n.
    """
    from collections import Counter

    try:
        returncode, stdout, stderr = common.run_command(command)
        text = stdout.decode('utf-8', 'ignore')
    except Exception:
        return []

    counter = Counter()
    in_job = False

    for line in text.split('\n'):
        if re.match(r'^\s*\d+(\[\d+\])?\s+\S+\s+PEND\s', line):
            in_job = True
            continue

        if re.match(r'^\s*JOBID\s', line):
            in_job = False
            continue

        if in_job:
            reason = line.strip()

            if not reason:
                in_job = False
                continue

            # Normalize: drop trailing ": N hosts;" / ";" and collapse digits.
            reason = re.sub(r'\s*:\s*\d+\s+hosts?\s*;?\s*$', '', reason)
            reason = re.sub(r'\s*;\s*$', '', reason)
            reason = re.sub(r'\b\d+\b', 'N', reason)

            if reason:
                counter[reason] += 1

    return [{'reason': r, 'count': c} for r, c in counter.most_common(top_n)]


def render_cluster_dashboard(metrics):
    """
    Render the deterministic data sections as an HTML fragment from the exact
    metrics dict. No LLM involvement -> fixed format, no run-to-run variance.
    Uses the CSS classes defined in wrap_html_report.

    One section 集群现状 holds everything: 总揽 / 利用率 panels plus the
    sub-parts 主机状态 / 队列负载 / 作业与用户分析 (rendered as <h3>).
    """
    import html as _html

    def esc(value):
        return _html.escape(str(value))

    def num(value):
        return 'N/A' if value is None else str(value)

    def pct(value):
        return 'N/A' if value is None else f'{value}%'

    def fmt_mem(gb):
        if gb is None:
            return 'N/A'

        if gb >= 1024:
            return f'{gb / 1024:.1f} TB'

        return f'{gb:.0f} GB'

    def card(value, label, cls='', sub=''):
        klass = ('card ' + cls).strip()
        sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ''
        return (f'<div class="{klass}"><div class="val">{esc(value)}</div>'
                f'<div class="lab">{esc(label)}</div>{sub_html}</div>')

    def gauge(percent, label):
        if percent is None:
            width, text, cls = 0, 'N/A', ''
        else:
            width, text = percent, f'{percent}%'
            cls = 'bad' if percent >= 85 else ('warn' if percent >= 60 else 'ok')

        bar_cls = ('bar ' + cls).strip()
        return (f'<div class="bar-row"><span class="name">{esc(label)}</span>'
                f'<div class="{bar_cls}"><span style="width:{width}%"></span></div>'
                f'<span class="pct">{esc(text)}</span></div>')

    states = metrics.get('host_states', {})
    total = metrics.get('host_total', 0) or 0
    open_n = states.get('open', 0)
    closed_n = total - open_n
    queues = sorted(metrics.get('queues', []), key=lambda q: q.get('njobs') or 0, reverse=True)

    parts = []

    # ---- 概览横幅: mechanical health verdict ----
    high_pend_queue = any((q['pend_rate'] >= 80 and q['pend'] > 0) for q in queues)
    closed_ratio = (closed_n / total) if total else 0

    if total == 0:
        banner_cls, verdict = 'warn', '无法获取主机数据'
    elif closed_ratio >= 0.3 or high_pend_queue:
        banner_cls, verdict = 'bad', '集群存在异常，建议尽快处理'
    elif closed_n > 0 or (metrics.get('jobs_pend') or 0) > 0:
        banner_cls, verdict = 'warn', '集群存在需关注项'
    else:
        banner_cls, verdict = 'ok', '集群运行正常'

    meta_bits = []

    if metrics.get('cluster'):
        meta_bits.append(f"{esc(metrics['cluster'])}")

    meta_bits.append(f"{total} 主机（open {open_n} / closed {closed_n}）")
    meta_bits.append(f"slot 利用率 {pct(metrics.get('util_slot'))}")
    meta_bits.append(f"运行 {num(metrics.get('jobs_run'))} / 排队 {num(metrics.get('jobs_pend'))}")
    parts.append(
        f'<div class="banner {banner_cls}"><div class="banner-v">{esc(verdict)}</div>'
        f'<div class="banner-m">{" · ".join(meta_bits)}</div></div>'
    )

    # ---- 集群现状: a single section; 主机状态/队列负载/作业与用户分析 are sub-parts ----
    parts.append('<h2 id="sec-status">集群现状</h2>')

    # 总揽: 主机数 / 总核数 / 总slots / 总内存 / 总作业数 (job summary last).
    run_n = metrics.get('jobs_run')
    pend_n = metrics.get('jobs_pend')
    susp_n = metrics.get('jobs_susp')
    job_total = (run_n or 0) + (pend_n or 0) + (susp_n or 0)
    scale_cards = [
        card(total, '主机数', 'ok' if closed_n == 0 else 'warn', f'open {open_n} · closed {closed_n}'),
        card(num(metrics.get('cores_total')), '总核数'),
        card(num(metrics.get('slots_total')), '总slots', sub=f"已用 {num(metrics.get('slots_used'))} · 空闲 {num(metrics.get('slots_idle'))}"),
        card(fmt_mem(metrics.get('mem_total_gb')), '总内存', sub=f"已用 {fmt_mem(metrics.get('mem_used_gb'))} · 空闲 {fmt_mem(metrics.get('mem_idle_gb'))}"),
        card(job_total, '总作业数', sub=f"RUN {num(run_n)} · PEND {num(pend_n)}"),
    ]
    parts.append('<div class="panel"><div class="panel-h">总揽</div><div class="cards">' + ''.join(scale_cards) + '</div></div>')

    # 利用率: horizontal gauge bars (clearer than donuts).
    gauges = (gauge(metrics.get('util_slot'), 'slot 利用率')
              + gauge(metrics.get('util_cpu'), 'cpu 利用率')
              + gauge(metrics.get('util_mem'), 'mem 利用率'))
    parts.append('<div class="panel"><div class="panel-h">利用率</div>' + gauges + '</div>')

    # ---- 主机状态 (sub-part): CSS bar chart + collapsible per-host detail ----
    parts.append('<h3 id="sec-hosts">主机状态</h3>')
    bar_order = [
        ('ok', open_n, 'ok'),
        ('closed_Admin', states.get('closed_Admin', 0), 'warn'),
        ('closed_Busy', states.get('closed_Busy', 0), ''),
        ('closed_Full', states.get('closed_Full', 0), 'bad'),
        ('Others', states.get('Others', 0), 'bad'),
    ]

    for name, count, cls in bar_order:
        width = round(count * 100 / total, 1) if total > 0 else 0
        bar_cls = ('bar ' + cls).strip()
        parts.append(
            f'<div class="bar-row"><span class="name">{esc(name)}</span>'
            f'<div class="{bar_cls}"><span style="width:{width}%"></span></div>'
            f'<span class="pct">{count} 台</span></div>'
        )

    hosts = metrics.get('hosts', [])

    if hosts:
        def metric_cell(value, threshold=90):
            if value is None:
                return '<td class="r">N/A</td>'

            if value >= threshold:
                return f'<td class="r"><span class="hot">{value}%</span></td>'

            return f'<td class="r">{value}%</td>'

        host_head = ('<tr><th>主机</th><th>状态</th><th class="r">slots总量</th>'
                     '<th class="r">slots用量</th><th class="r">cpu%</th><th class="r">mem%</th></tr>')
        rows = []

        for h in sorted(hosts, key=lambda x: (x['status'] == 'ok', x['name'])):
            abnormal = h['status'] != 'ok'
            status_cell = (f'<span class="hot">{esc(h["status"])}</span>' if abnormal
                           else esc(h['status']))
            rows.append(
                f'<tr><td>{esc(h["name"])}</td><td>{status_cell}</td>'
                f'<td class="r">{esc(h["max"])}</td>'
                f'<td class="r">{esc(h["njobs"])}</td>'
                f'{metric_cell(h["ut"])}{metric_cell(h["mem"])}</tr>'
            )

        parts.append(
            f'<details class="collapsible"><summary>展开主机明细（{len(hosts)} 台）</summary>'
            '<table class="sortable"><thead>' + host_head + '</thead><tbody>'
            + ''.join(rows) + '</tbody></table></details>'
        )

    # ---- 队列负载 (sub-part): table + composition bars; >10 queues collapse ----
    parts.append('<h3 id="sec-queues">队列负载</h3>')

    if queues:
        head = ('<tr><th>队列名</th><th>优先级</th><th>状态</th><th>队列slots</th>'
                '<th class="r">总作业</th><th class="r">运行</th><th class="r">排队</th>'
                '<th class="r">挂起</th><th class="r">排队率</th></tr>')

        def qrow(q, cls=''):
            rate = q['pend_rate']
            rate_cell = (f'<td class="r"><span class="hot">{rate}%</span></td>' if rate >= 50
                         else f'<td class="r">{rate}%</td>')
            tr = f'<tr class="{cls}">' if cls else '<tr>'
            return (f'{tr}<td>{esc(q["name"])}</td><td class="r">{esc(q.get("prio", ""))}</td>'
                    f'<td>{esc(q.get("status", ""))}</td><td class="r">{esc(q["slots"])}</td>'
                    f'<td class="r">{q["njobs"]}</td><td class="r">{q["run"]}</td>'
                    f'<td class="r">{q["pend"]}</td><td class="r">{q.get("susp", 0)}</td>{rate_cell}</tr>')

        q_limit = 10
        visible_q = queues[:q_limit]
        hidden_q = queues[q_limit:]

        # Single continuous table; hidden queues are extra <tr> rows toggled by a
        # pure-CSS checkbox hack (the <input> must precede <table> as a sibling).
        body = ''.join(qrow(q) for q in visible_q) + ''.join(qrow(q, 'extra') for q in hidden_q)

        if hidden_q:
            toggle = (
                '<input type="checkbox" id="q-more" class="row-toggle">'
                f'<table class="sortable" data-collapse="{q_limit}"><thead>' + head + '</thead><tbody>' + body + '</tbody>'
                '<tfoot><tr><td colspan="9">'
                f'<label class="more-toggle" for="q-more"><span class="ico">&#9656;</span>'
                f'<span class="show">展开其余 {len(hidden_q)} 个队列</span>'
                '<span class="ico hide">&#9662;</span><span class="hide">收起</span>'
                '</label></td></tr></tfoot></table>'
            )
            parts.append(toggle)
        else:
            parts.append('<table class="sortable"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>')

        def comp_row(q):
            njobs = q['njobs'] or 0

            if njobs > 0:
                rw = round(q['run'] * 100 / njobs, 1)
                pw = round(q['pend'] * 100 / njobs, 1)
                sw = round(q.get('susp', 0) * 100 / njobs, 1)
                segs = (f'<span class="seg run" style="width:{rw}%"></span>'
                        f'<span class="seg pend" style="width:{pw}%"></span>'
                        f'<span class="seg susp" style="width:{sw}%"></span>')
            else:
                segs = ''

            return (f'<div class="bar-row"><span class="name">{esc(q["name"])}</span>'
                    f'<div class="bar stack">{segs}</div>'
                    f'<span class="pct">{njobs}</span></div>')

        comp = ['<div class="panel"><div class="panel-h">各队列作业构成（绿=运行 橙=排队 灰=挂起）</div>']

        if hidden_q:
            comp.append('<input type="checkbox" id="c-more" class="comp-toggle">')

        comp.append(''.join(comp_row(q) for q in visible_q))

        if hidden_q:
            comp.append('<div class="comp-extra">' + ''.join(comp_row(q) for q in hidden_q) + '</div>')
            comp.append(
                f'<label class="comp-more" for="c-more"><span class="ico">&#9656;</span>'
                f'<span class="show">展开其余 {len(hidden_q)} 个队列</span>'
                '<span class="ico hide">&#9662;</span><span class="hide">收起</span></label>'
            )

        comp.append('</div>')
        parts.append(''.join(comp))
    else:
        parts.append('<p>（无队列数据）</p>')

    # ---- 作业与用户分析 (sub-part): pending reasons + active users ----
    reasons = metrics.get('pending_reasons', [])
    users = metrics.get('active_users', [])

    if reasons or users:
        parts.append('<h3 id="sec-jobs">作业与用户分析</h3>')
        parts.append('<div class="two-col">')

        # Pending reasons.
        block = ['<div class="panel"><div class="panel-h">排队原因 Top</div>']

        if reasons:
            rr = ['<table class="sortable"><thead><tr><th>排队原因</th><th class="r">作业数</th></tr></thead><tbody>']

            for item in reasons:
                rr.append(f'<tr><td>{esc(item["reason"])}</td><td class="r">{item["count"]}</td></tr>')

            rr.append('</tbody></table>')
            block.append(''.join(rr))
        else:
            block.append('<p>无排队作业。</p>')

        block.append('</div>')
        parts.append(''.join(block))

        # Active users.
        block = ['<div class="panel"><div class="panel-h">活跃用户 Top</div>']

        if users:
            uu = ['<table class="sortable"><thead><tr><th>用户</th><th class="r">运行</th><th class="r">排队</th><th class="r">总作业</th></tr></thead><tbody>']

            for u in users:
                uu.append(f'<tr><td>{esc(u["user"])}</td><td class="r">{u["run"]}</td>'
                          f'<td class="r">{u["pend"]}</td><td class="r">{u["njobs"]}</td></tr>')

            uu.append('</tbody></table>')
            block.append(''.join(uu))
        else:
            block.append('<p>当前无活跃作业。</p>')

        block.append('</div>')
        parts.append(''.join(block))
        parts.append('</div>')

    return '\n'.join(parts)


def extract_final_text(messages):
    """Return the last assistant text message content, stripped of markdown code fences."""
    for msg in reversed(messages):
        if msg.get('role') == 'assistant':
            content = msg.get('content')

            if isinstance(content, str) and content.strip():
                text = content.strip()
                # Strip a leading/trailing ```html ... ``` fence if the model added one.
                text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
                text = re.sub(r'\s*```$', '', text)
                # Drop any chatty preamble before the first HTML block tag.
                match = re.search(r'<(?:h[1-6]|p|div|ul|ol|table)\b', text, re.IGNORECASE)

                if match:
                    text = text[match.start():]
                else:
                    # Model returned plain text (no HTML): escape and wrap in <pre>
                    # so it renders readably instead of breaking the report layout.
                    import html as _html
                    text = '<pre>' + _html.escape(text) + '</pre>'

                return text.strip()

    return ''


def _run_readonly_agent(messages, api_base_url, api_key, model_name,
                        db_path='', lmstat_path='lmstat', lmstat_bsub_command='',
                        doc_chunks=None, embedding_model='', embedding_api_base_url='',
                        embedding_api_key='', debug=False, on_thread_created=None):
    """Run the read-only agent loop synchronously; return (final_text, thread).

    Centralizes the AiChatThread construction + error capture shared by every
    report generator. confirm_mode='auto_reject' rejects state-changing commands
    instead of prompting (reports are headless). max_loops=3 keeps the analyze
    phase to a few tool round-trips.
    """
    thread = AiChatThread(
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_name,
        messages=messages,
        db_path=db_path,
        license_dic={},
        lmstat_path=lmstat_path,
        lmstat_bsub_command=lmstat_bsub_command,
        doc_chunks=doc_chunks,
        skills=[],
        embedding_model=embedding_model,
        embedding_api_base_url=embedding_api_base_url,
        embedding_api_key=embedding_api_key,
        debug=debug,
        confirm_mode='auto_reject',
        max_tokens=16384,
        max_loops=3,
    )

    if on_thread_created is not None:
        on_thread_created(thread)

    # AiChatThread.run() swallows agent-loop errors into error_signal; capture
    # them so the caller can surface the real cause instead of a generic empty
    # report.
    errors = []
    thread.error_signal.connect(errors.append)
    thread.run()

    final_text = extract_final_text(thread.messages)

    if not final_text and errors:
        raise RuntimeError(errors[-1])

    # Fallback: LLMs occasionally omit the required 分析汇总 section (even when
    # output is short — this is an instruction-following lapse, not a token
    # truncation). Append a minimal 分析汇总 skeleton so the report is never
    # missing the section, keeping the structure (and the side-nav anchor) intact.
    final_text = _ensure_summary_section(final_text)

    return final_text, thread


def _ensure_summary_section(html_fragment):
    """Ensure the LLM output ends with an <h2>分析汇总</h2> section.

    If the section is missing, append a minimal skeleton:
      - 总体评估: a neutral placeholder (the LLM omitted the real assessment).
      - 当前严重问题: extracted from the issue card titles already present.
      - 系统管理员 TODO / 用户 TODO: left empty (LLM omitted actionable items).
    """
    if not html_fragment or not html_fragment.strip():
        return html_fragment

    # Match the section heading loosely (covers <h2>分析汇总</h2>, <h2 id="...">分析汇总</h2>).
    if re.search(r'<h2[^>]*>\s*分析汇总\s*</h2>', html_fragment):
        return html_fragment

    # Extract issue card titles (the <b>...</b> right after the badge span) so
    # the severe-issue list reflects what the LLM already found.
    titles = re.findall(r'<span class="badge high[^>]*>严重</span>\s*<b>([^<]+)</b>', html_fragment)
    severe_items = ''.join(f'<li>{t}</li>' for t in titles) or '<li>无</li>'

    skeleton = (
        '\n<h2>分析汇总</h2>\n'
        '<div class="card-panel assess"><b>总体评估：</b>（AI 未生成完整汇总，以下为补全骨架，请结合上方“状态分析”核对。）</div>\n'
        f'<div class="card-panel severe"><b>当前严重问题</b><ul>{severe_items}</ul></div>\n'
        '<div class="two-col">\n'
        '  <div class="panel admin"><div class="panel-h">系统管理员 TODO</div><ul><li>无</li></ul></div>\n'
        '  <div class="panel user"><div class="panel-h">用户 TODO</div><ul><li>无</li></ul></div>\n'
        '</div>\n'
    )

    return html_fragment.rstrip() + '\n' + skeleton


def generate_cluster_analyze_report(api_base_url, api_key, model_name, tool='lsf',
                                     db_path='', lmstat_path='lmstat', lmstat_bsub_command='',
                                     doc_chunks=None, embedding_model='', embedding_api_base_url='',
                                     embedding_api_key='', debug=False, on_thread_created=None):
    """
    Headless: collect a cluster snapshot, run the read-only agent loop, return the HTML
    report fragment. Reuses AiChatThread's agent loop synchronously (no QThread.start()).

    on_thread_created: optional callback receiving the inner AiChatThread so a caller
    (e.g. the GUI) can request cancellation via thread.stop().
    """
    import time as _time
    from PyQt5.QtCore import QCoreApplication

    _t_total_start = _time.time()

    # AiChatThread is a QThread (QObject); ensure an application object exists for headless use.
    if QCoreApplication.instance() is None:
        generate_cluster_analyze_report._app = QCoreApplication([])

    # Parallel data collection: all LSF commands run concurrently, results shared
    # between compute_cluster_metrics and collect_cluster_snapshot (no duplication).
    _t_collect = _time.time()
    raw_data = _collect_raw_data(tool)
    _t_collect_end = _time.time()

    # Exact, deterministic data sections (集群现状/主机状态/队列负载) are computed and
    # rendered in Python so they never vary run-to-run. The LLM only writes the
    # analyze sections (集群问题/分析汇总) from these authoritative numbers.
    metrics = compute_cluster_metrics(tool, raw_data=raw_data)
    dashboard_html = render_cluster_dashboard(metrics)

    states = metrics.get('host_states', {})
    metrics_text = (
        "已由系统精确计算的权威指标（请直接引用，禁止重新估算或推翻）：\n"
        f"- 主机：总数={metrics.get('host_total')}, open={states.get('open')}, "
        f"closed_Admin={states.get('closed_Admin')}, closed_Busy={states.get('closed_Busy')}, "
        f"closed_Full={states.get('closed_Full')}, Others={states.get('Others')}\n"
        f"- Slots：总={metrics.get('slots_total')}, 已用={metrics.get('slots_used')}, 空闲={metrics.get('slots_idle')}\n"
        f"- 作业：运行={metrics.get('jobs_run')}, 排队={metrics.get('jobs_pend')}\n"
        f"- 利用率：slot={metrics.get('util_slot')}%, cpu={metrics.get('util_cpu')}%, mem={metrics.get('util_mem')}%\n"
        f"- 队列（名:slots/总/排队/运行/排队率）："
        + '; '.join(
            f"{q['name']}:{q['slots']}/{q['njobs']}/{q['pend']}/{q['run']}/{q['pend_rate']}%"
            for q in metrics.get('queues', [])
        )
    )

    snapshot = collect_cluster_snapshot(tool, raw_data=raw_data)

    user_prompt = (
        "下面提供两部分输入：(A) 系统已精确计算的权威指标，(B) 集群原始快照数据。\n"
        "报告的“集群现状/主机状态/队列负载”三段已由系统生成，你不要重复输出这三段。\n"
        "你只需基于以下数据，生成“集群问题”和“分析汇总”两段（详见 system 指令的格式要求）。\n"
        "可调用只读命令（如 bjobs/bqueues -l/bhosts -l/lsload -l 等）下钻确认问题，"
        "但不要分析 license，也不要执行任何改变集群状态的命令。\n\n"
        f"===== (A) 权威指标 =====\n{metrics_text}\n===== 指标结束 =====\n\n"
        f"===== (B) 集群快照 =====\n{snapshot}\n===== 快照结束 =====\n"
    )

    messages = [
        {"role": "system", "content": CLUSTER_ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # Run the agent loop synchronously in the current thread.
    _t_llm = _time.time()
    analyze_html, thread = _run_readonly_agent(
        messages,
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_name,
        db_path=db_path,
        lmstat_path=lmstat_path,
        lmstat_bsub_command=lmstat_bsub_command,
        doc_chunks=doc_chunks,
        embedding_model=embedding_model,
        embedding_api_base_url=embedding_api_base_url,
        embedding_api_key=embedding_api_key,
        debug=debug,
        on_thread_created=on_thread_created,
    )
    _t_llm_end = _time.time()

    _t_total_end = _time.time()

    # Timing summary appended to the report.
    timing_stats = thread._timing_stats
    timing_html = (
        f'<div class="header-info" style="margin-top:40px; padding-top:12px; border-top:1px solid var(--line, #e0e0e0);">'
        f'<p>耗时统计 — '
        f'数据采集 {_t_collect_end - _t_collect:.1f}s | '
        f'LLM分析 {_t_llm_end - _t_llm:.1f}s '
        f'(调用{timing_stats["llm_calls"]}次, 首token最大延迟{timing_stats["llm_first_token_max"]:.1f}s, '
        f'生成{timing_stats["output_tokens"]}tokens) | '
        f'总耗时 {_t_total_end - _t_total_start:.1f}s</p></div>'
    )

    # Deterministic data sections first, then the LLM's analyze sections.
    return dashboard_html + '\n' + analyze_html + '\n' + timing_html


def resolve_report_dir(report_type):
    """Return a writable directory for AI reports.

    report_type: 'cluster' / 'user' / 'job' / 'queue'.
    Returns <ai_db_path>/ai_report/<report_type>/, created 0o1777 so every
    user can write their own reports. <ai_db_path> is resolved via
    common_db_path (config_ai.db_path > config.py db_path/ai > <install>/db/ai),
    which is 0o1777 at the top level — so ordinary users can always create the
    ai_report subdir (unlike the old <cluster_db_path>/ai_report, which needed
    write on the 0o755 cluster dir and fell back to /tmp for non-owners).

    Falls back to a per-user /tmp dir on permission failure so report
    generation never fails.
    """
    import getpass
    import tempfile

    from common import common_config, common_db_path

    config_ai = common_config.load_config('ai')
    ai_db = common_db_path.resolve_db_path(config_ai, 'ai')

    candidates = [
        os.path.join(str(ai_db), 'ai_report', report_type),
        os.path.join(tempfile.gettempdir(), 'lsfMonitor_' + getpass.getuser(), 'ai_report', report_type),
    ]

    for report_dir in candidates:
        try:
            # 0o1777 (sticky) so every user can write their own reports but
            # cannot delete others'. Without it, makedirs defaults to 0o755
            # and only the first user to create the dir can save reports.
            os.makedirs(report_dir, mode=0o1777, exist_ok=True)

            # chmod in case the dir already existed with a tighter mode.
            os.chmod(report_dir, 0o1777)

            # makedirs 的 mode 只作用于叶子(<type>)目录,中间的 ai_report
            # 按 umask 创建为 0o755,会阻止其他用户在其下新建 <type> 子目录,
            # 故一并 chmod 中间层。
            os.chmod(os.path.dirname(report_dir), 0o1777)

            if os.access(report_dir, os.W_OK):
                return report_dir
        except Exception:
            continue

    # Last resort: a unique temp dir, which mkdtemp guarantees is writable.
    return tempfile.mkdtemp(prefix=f'lsfMonitor_{report_type}_report_')


def wrap_html_report(content, heading='集群分析报告', meta_line=''):
    """Wrap an HTML fragment into a full styled HTML page."""
    import re as _re

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    meta = meta_line if meta_line else f'Generated: {timestamp}'

    # Per-report nav config: maps each LLM-generated section title to a nav id.
    # Headings are also injected with matching id attributes below so the nav
    # anchors scroll to them. A nav item of (label, id, is_sub) renders as a
    # sub-entry (indented) when is_sub is True.
    _report_nav = {
        '集群分析报告': [
            ('集群现状', 'sec-status', False),
            ('主机状态', 'sec-hosts', True),
            ('队列负载', 'sec-queues', True),
            ('作业与用户分析', 'sec-jobs', True),
            ('集群问题', 'sec-issues', False),
            ('分析汇总', 'sec-summary', False),
        ],
        '用户作业分析报告': [
            ('作业画像', 'sec-profile', False),
            ('作业概览', 'sec-overview', False),
            ('PEND 作业分析', 'sec-pend', False),
            ('RUN 作业性能', 'sec-run', False),
            ('EXIT 作业分析', 'sec-exit', False),
            ('DONE 作业分析', 'sec-done', False),
            ('PSUSP 作业分析', 'sec-psusp', False),
            ('USUSP 作业分析', 'sec-ususp', False),
            ('SSUSP 作业分析', 'sec-ssusp', False),
            ('UNKWN 作业分析', 'sec-unkwn', False),
            ('WAIT 作业分析', 'sec-wait', False),
            ('ZOMBI 作业分析', 'sec-zombi', False),
            ('PROV 作业分析', 'sec-prov', False),
            ('集群问题反推', 'sec-cluster', False),
            ('分析汇总', 'sec-summary', False),
        ],
        '作业分析报告': [
            ('作业信息', 'sec-info', False),
            ('mem/idle_factor 曲线', 'sec-curve', False),
            ('状态分析', 'sec-analysis', False),
            ('分析汇总', 'sec-summary', False),
        ],
        '队列分析报告': [
            ('队列现状', 'sec-status', False),
            ('队列限制配置', 'sec-config', False),
            ('主机状态', 'sec-hosts', False),
            ('排队原因', 'sec-jobs', False),
            ('队列问题', 'sec-issues', False),
            ('分析汇总', 'sec-summary', False),
        ],
    }

    nav_items = _report_nav.get(heading, _report_nav['集群分析报告'])

    # Inject id attributes into h2 headings that lack them, for every nav target.
    _heading_id_map = {label: hid for label, hid, _ in nav_items}

    for h_text, h_id in _heading_id_map.items():
        content = _re.sub(
            r'<h2(?!\s[^>]*id=)([^>]*)>' + _re.escape(h_text) + r'</h2>',
            rf'<h2 id="{h_id}"\1>{h_text}</h2>',
            content
        )

    # Strip any <script> tags the LLM may have injected into its HTML output —
    # they would interfere with wrap_html_report's own scripts and can cause
    # "Uncaught SyntaxError: Unexpected token '}'" in the browser console.
    content = _re.sub(r'<script[^>]*>.*?</script>', '', content, flags=_re.DOTALL | _re.IGNORECASE)

    # Keep only nav items whose section actually appears in the content (LLM
    # skips states with zero jobs, so their h2 won't exist — don't show dead
    # nav links).
    _present_ids = set(_re.findall(r'<h2[^>]*id="([^"]+)"', content))
    nav_items = [(label, hid, sub) for label, hid, sub in nav_items if hid in _present_ids]

    # Click-to-sort for tables marked .sortable: numeric columns sort by value,
    # others by string. Self-contained vanilla JS (offline-safe, no deps).
    script = r'''<script>
(function () {
  function parseNum(s) {
    var t = (s || '').replace(/[,%\s]/g, '');
    if (!t) return null;
    var m = t.match(/^[+-]?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : null;
  }
  function blankish(t) {
    t = (t || '').trim();
    return t === '' || t === 'N/A' || t === '-';
  }
  function sortTable(table, idx, th) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    // Numeric column if at least one cell is a number and every other cell is
    // either numeric or a blank placeholder (N/A / - / empty). This keeps
    // cpu%/mem% columns numeric even when some hosts report N/A.
    var cells = rows.map(function (r) {
      return r.cells[idx] ? r.cells[idx].textContent : '';
    });
    var numeric = cells.some(function (t) { return parseNum(t) !== null; })
      && cells.every(function (t) { return parseNum(t) !== null || blankish(t); });
    var asc = th.getAttribute('data-dir') !== 'asc';
    rows.sort(function (a, b) {
      var x = a.cells[idx] ? a.cells[idx].textContent.trim() : '';
      var y = b.cells[idx] ? b.cells[idx].textContent.trim() : '';
      if (numeric) {
        var nx = parseNum(x), ny = parseNum(y);
        // Blank/N/A values always sort to the bottom regardless of direction.
        if (nx === null && ny === null) return 0;
        if (nx === null) return 1;
        if (ny === null) return -1;
        return asc ? (nx - ny) : (ny - nx);
      }
      var r = x.localeCompare(y, 'zh');
      return asc ? r : -r;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    var limit = parseInt(table.getAttribute('data-collapse') || '0', 10);
    if (limit > 0) {
      rows.forEach(function (r, i) {
        if (i < limit) r.classList.remove('extra'); else r.classList.add('extra');
      });
    }
    Array.prototype.forEach.call(th.parentNode.cells, function (c) {
      c.removeAttribute('data-dir');
    });
    th.setAttribute('data-dir', asc ? 'asc' : 'desc');
  }
  document.querySelectorAll('table').forEach(function (table) {
    var thead = table.tHead;
    if (!thead || !thead.rows.length) return;
    table.classList.add('sortable');
    Array.prototype.forEach.call(thead.rows[0].cells, function (th, idx) {
      th.addEventListener('click', function () { sortTable(table, idx, th); });
    });
  });
})();

// Command cell: click toggles full-content wrap (Excel-like expand/collapse).
document.querySelectorAll('td.cmd-cell').forEach(function (cell) {
  cell.addEventListener('click', function () { cell.classList.toggle('expanded'); });
});
</script>
<script>
(function () {
  var nav = document.querySelector('.side-nav');
  if (!nav) return;
  var links = nav.querySelectorAll('a[href^="#"]');
  var sections = [];
  links.forEach(function (a) {
    var id = a.getAttribute('href').slice(1);
    var el = document.getElementById(id);
    if (el) sections.push({ el: el, link: a });
  });
  if (!sections.length) return;
  function onScroll() {
    var scrollY = window.scrollY || window.pageYOffset;
    var active = sections[0];
    for (var i = 0; i < sections.length; i++) {
      if (sections[i].el.offsetTop - 80 <= scrollY) active = sections[i];
    }
    links.forEach(function (a) { a.classList.remove('active'); });
    if (active) active.link.classList.add('active');
  }
  window.addEventListener('scroll', onScroll);
  onScroll();
  // Smooth scroll on nav click
  links.forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href').slice(1);
      var target = document.getElementById(id);
      if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      // Collapse the nav after following a link on narrow screens.
      closeNav();
    });
  });

  // Hamburger toggle: narrow screens hide the side-nav; the ☰ button slides
  // it in/out with a backdrop. No-op on wide screens (button is hidden).
  var toggleBtn = document.querySelector('.nav-toggle');
  var backdrop = document.querySelector('.nav-backdrop');

  function openNav() {
    nav.classList.add('open');
    if (backdrop) backdrop.classList.add('show');
  }

  function closeNav() {
    nav.classList.remove('open');
    if (backdrop) backdrop.classList.remove('show');
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', function () {
      nav.classList.contains('open') ? closeNav() : openNav();
    });
  }

  if (backdrop) {
    backdrop.addEventListener('click', closeNav);
  }
})();
</script>'''

    # Build the side-nav links from the per-report nav config.
    nav_lines = []

    for label, hid, is_sub in nav_items:
        cls = ' class="sub"' if is_sub else ''
        nav_lines.append(f'<a href="#{hid}"{cls}>{label}</a>')

    nav_html = '\n'.join(nav_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{heading}</title>
<style>
:root {{ --blue:#3498db; --blue-d:#2980b9; --ink:#2c3e50; --muted:#7f8c8d;
        --red:#e74c3c; --orange:#e67e22; --green:#27ae60; --line:#e3e8ee;
        --bg:#eef2f6; --surface:#ffffff; --surface-2:#f6f9fc; --surface-3:#fafcfe;
        --track:#e9eef3; --shadow:rgba(15,30,50,.06); --code-bg:#eef2f7; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --blue:#4aa3df; --blue-d:#5dade2; --ink:#e6edf3; --muted:#94a3b3;
          --red:#ef6e63; --orange:#ec9b4d; --green:#3fc77a; --line:#283341;
          --bg:#0e131a; --surface:#161d27; --surface-2:#1b232f; --surface-3:#1b232f;
          --track:#222d3b; --shadow:rgba(0,0,0,.45); --code-bg:#202a37; }}
}}
* {{ box-sizing: border-box; }}
body {{ font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif; color: var(--ink);
        margin: 0; padding: 0; background: var(--bg); line-height: 1.7;
        -webkit-font-smoothing: antialiased; }}
.wrap {{ max-width: 1080px; margin: 0 auto; padding: 32px 28px 60px 28px; }}
@media (min-width: 1360px) {{ .wrap {{ margin-left: 260px; }} }}
h1 {{ color: var(--ink); font-size: 26px; margin: 0 0 6px; letter-spacing: .01em; }}
h2 {{ color: var(--ink); font-size: 19px; margin: 38px 0 14px; padding: 2px 0 9px 13px;
      border-left: 4px solid var(--blue); border-bottom: 1px solid var(--line);
      letter-spacing: .02em; }}
h3 {{ color: var(--ink); font-size: 15.5px; font-weight: 600; margin: 26px 0 10px;
      padding-left: 11px; border-left: 3px solid var(--muted); opacity: .92; }}
p {{ margin: 10px 0; }}
.header-info {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
.card-panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
              padding: 20px 24px; margin-bottom: 18px; box-shadow: 0 1px 3px var(--shadow); }}

/* 概览指标卡片 */
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
         gap: 14px; margin: 14px 0; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
        padding: 16px 18px; box-shadow: 0 1px 3px var(--shadow); }}
.card .val {{ font-size: 28px; font-weight: 700; color: var(--blue-d); font-variant-numeric: tabular-nums; }}
.card .lab {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}
.card .sub {{ font-size: 12px; color: var(--muted); margin-top: 6px; opacity: .85; }}
.card.warn .val {{ color: var(--orange); }}
.card.bad .val {{ color: var(--red); }}
.card.ok .val {{ color: var(--green); }}

/* 表格 */
table {{ border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px;
        background: var(--surface); border-radius: 10px; overflow: hidden;
        box-shadow: 0 1px 3px var(--shadow); }}
th {{ background: linear-gradient(135deg, var(--blue), var(--blue-d)); color: #fff;
     padding: 10px 12px; text-align: left; font-weight: 600; letter-spacing: .02em; }}
td {{ padding: 9px 12px; border-bottom: 1px solid var(--line); }}
tr:last-child td {{ border-bottom: none; }}
tr:nth-child(even) td {{ background: var(--surface-2); }}
/* Command 单元格：超长 Excel 式省略，悬停 tooltip 看全，点击展开换行 */
td.cmd-cell {{ max-width: 480px; white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; cursor: pointer; font-family: Consolas, "SFMono-Regular", monospace; }}
td.cmd-cell.expanded {{ white-space: normal; word-break: break-all; }}

/* 横向条形图（纯 CSS，离线可用） */
.bar-row {{ display: flex; align-items: center; gap: 10px; margin: 7px 0; font-size: 14px; }}
.bar-row .name {{ flex: 0 0 160px; color: var(--ink); }}
.bar {{ flex: 1; background: var(--track); border-radius: 5px; height: 20px; position: relative; overflow: hidden; }}
.bar > span {{ display: block; height: 100%; background: var(--blue);
              border-radius: 5px 0 0 5px; }}
.bar.warn > span {{ background: var(--orange); }}
.bar.bad > span {{ background: var(--red); }}
.bar.ok > span {{ background: var(--green); }}
.bar-row .pct {{ flex: 0 0 56px; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }}

/* 概览横幅 */
.banner {{ border-radius: 10px; padding: 16px 20px; margin: 4px 0 22px; color: #fff;
          display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
          box-shadow: 0 2px 6px rgba(0,0,0,.08); }}
.banner.ok {{ background: linear-gradient(135deg, #27ae60, #2ecc71); }}
.banner.warn {{ background: linear-gradient(135deg, #e67e22, #f39c12); }}
.banner.bad {{ background: linear-gradient(135deg, #e74c3c, #c0392b); }}
.banner-v {{ font-size: 18px; font-weight: 700; }}
.banner-m {{ font-size: 13px; opacity: .94; }}

/* 分组面板 */
.panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
         padding: 6px 18px 16px; margin: 14px 0; box-shadow: 0 1px 3px var(--shadow); }}
.panel-h {{ font-size: 12px; font-weight: 600; color: var(--muted); letter-spacing: .06em;
           text-transform: uppercase; margin: 14px 2px 6px; display: flex; align-items: center; gap: 7px; }}
.panel-h::before {{ content: ''; width: 7px; height: 7px; border-radius: 50%;
                   background: var(--blue); flex: 0 0 auto; }}
.panel .cards {{ margin: 4px 0 0; }}
.panel .card {{ box-shadow: none; background: var(--surface-3); }}
.panel table {{ box-shadow: none; }}

/* 可折叠区块（主机明细） */
.collapsible {{ margin: 10px 0 4px; }}
.collapsible > summary {{ cursor: pointer; font-size: 13px; color: var(--blue-d);
                         font-weight: 600; padding: 8px 12px; background: var(--surface-3);
                         border: 1px solid var(--line); border-radius: 8px; list-style: none;
                         user-select: none; }}
.collapsible > summary::-webkit-details-marker {{ display: none; }}
.collapsible > summary::before {{ content: '\\25B8  '; color: var(--muted); }}
.collapsible[open] > summary::before {{ content: '\\25BE  '; }}
.collapsible[open] > summary {{ border-radius: 8px 8px 0 0; }}
.collapsible table {{ margin-top: 0; border-radius: 0 0 8px 8px; }}
.collapsible .bar-row {{ margin-top: 9px; }}

/* 单表/单面板行折叠（队列负载）：纯 CSS checkbox hack，离线无 JS */
.row-toggle, .comp-toggle {{ display: none; }}
tr.extra {{ display: none; }}
.comp-extra {{ display: none; }}
.row-toggle:checked ~ table tr.extra {{ display: table-row; }}
.comp-toggle:checked ~ .comp-extra {{ display: block; }}
tfoot td {{ padding: 0; border-top: 1px solid var(--line); border-bottom: none; }}
.more-toggle {{ display: block; cursor: pointer; user-select: none; text-align: center;
               font-size: 13px; color: var(--blue-d); font-weight: 600; padding: 9px 12px;
               background: var(--surface-3); }}
.more-toggle:hover, .comp-more:hover {{ background: var(--surface-2); }}
.comp-more {{ display: block; cursor: pointer; user-select: none; text-align: center; margin-top: 10px;
             font-size: 13px; color: var(--blue-d); font-weight: 600; padding: 8px 12px;
             background: var(--surface-3); border: 1px solid var(--line); border-radius: 8px; }}
.more-toggle .ico, .comp-more .ico {{ color: var(--muted); margin-right: 4px; }}
.more-toggle .hide, .comp-more .hide {{ display: none; }}
.row-toggle:checked ~ table .more-toggle .show {{ display: none; }}
.row-toggle:checked ~ table .more-toggle .hide {{ display: inline; }}
.comp-toggle:checked ~ .comp-more .show {{ display: none; }}
.comp-toggle:checked ~ .comp-more .hide {{ display: inline; }}

/* 堆叠条形（队列作业构成） */
.bar.stack {{ display: flex; }}
.bar .seg {{ height: 100%; border-radius: 0; }}
.bar .seg.run {{ background: var(--green); }}
.bar .seg.pend {{ background: var(--orange); }}
.bar .seg.susp {{ background: var(--muted); }}

/* 双列布局：两框等高（align-items: stretch + 子项满高） */
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: stretch; }}
.two-col > .panel {{ margin: 0; height: 100%; min-width: 0; overflow: hidden; }}
@media (max-width: 720px) {{ .two-col {{ grid-template-columns: 1fr; }} }}

/* 表格数值列 */
td.r, th.r {{ text-align: right; font-variant-numeric: tabular-nums; }}
.hot {{ color: var(--red); font-weight: 700; }}

/* 可排序表格：点击表头排序（数字按数值、其它按字符串） */
table.sortable thead th {{ cursor: pointer; user-select: none; white-space: nowrap; }}
table.sortable thead th:hover {{ background: linear-gradient(135deg, var(--blue-d), var(--blue)); }}
table.sortable thead th::after {{ content: '\\2195'; opacity: .4; margin-left: 6px; font-size: 11px; }}
table.sortable thead th[data-dir="asc"]::after {{ content: '\\2191'; opacity: 1; }}
table.sortable thead th[data-dir="desc"]::after {{ content: '\\2193'; opacity: 1; }}

/* 分析汇总：按类型加浅背景色 + 左侧强调条 */
.card-panel.assess {{ background: var(--surface);
    background: color-mix(in srgb, var(--blue) 7%, var(--surface)); border-left: 4px solid var(--blue); }}
.card-panel.severe {{ background: var(--surface);
    background: color-mix(in srgb, var(--red) 8%, var(--surface)); border-left: 4px solid var(--red); }}
.panel.admin, .panel.user {{ background: var(--surface);
    background: color-mix(in srgb, var(--green) 8%, var(--surface)); border-left: 4px solid var(--green); }}

/* 问题卡片与严重度标记 */
.issue {{ background: var(--surface); border: 1px solid var(--line); border-left: 5px solid var(--blue);
         border-radius: 10px; padding: 14px 18px; margin: 12px 0;
         box-shadow: 0 1px 3px var(--shadow); }}
.issue.high {{ border-left-color: var(--red); }}
.issue.mid {{ border-left-color: var(--orange); }}
.issue.low {{ border-left-color: var(--green); }}
.badge {{ display: inline-block; font-size: 12px; font-weight: 600; color: #fff;
         padding: 2px 9px; border-radius: 11px; margin-right: 8px; vertical-align: middle; }}
.badge.high {{ background: var(--red); }}
.badge.mid {{ background: var(--orange); }}
.badge.low {{ background: var(--green); }}

code {{ background: var(--code-bg); padding: 2px 5px; border-radius: 4px; font-size: 13px;
       font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }}
pre {{ background: #1f2b38; color: #ecf0f1; padding: 12px 14px; border-radius: 8px;
      overflow-x: auto; font-size: 13px; line-height: 1.5; }}
pre code {{ background: none; color: inherit; padding: 0; }}
ul, ol {{ padding-left: 22px; }}
li {{ margin: 6px 0; }}

/* 左侧导航栏 */
.side-nav {{ position: fixed; top: 0; left: 0; width: 220px; height: 100vh; overflow-y: auto;
            background: var(--surface); border-right: 1px solid var(--line); padding: 24px 0;
            box-shadow: 2px 0 8px var(--shadow); z-index: 100;
            transition: transform .25s ease; }}
.side-nav .nav-title {{ font-size: 14px; font-weight: 700; color: var(--ink); padding: 0 18px 14px;
                       border-bottom: 1px solid var(--line); margin-bottom: 10px; }}
.side-nav a {{ display: block; padding: 9px 18px; font-size: 13px; color: var(--muted);
              text-decoration: none; border-left: 3px solid transparent; transition: all .15s; }}
.side-nav a:hover {{ color: var(--ink); background: var(--surface-2); }}
.side-nav a.active {{ color: var(--blue-d); border-left-color: var(--blue); font-weight: 600;
                     background: color-mix(in srgb, var(--blue) 6%, var(--surface)); }}
.side-nav a.sub {{ padding-left: 32px; font-size: 12.5px; }}

/* 汉堡按钮 + 遮罩：窄屏(<=1359px)下导航收起，点按钮展开。宽屏导航常驻、按钮隐藏。 */
.nav-toggle {{ display: none; position: fixed; top: 12px; left: 12px; z-index: 200;
              width: 38px; height: 38px; border: 1px solid var(--line); border-radius: 8px;
              background: var(--surface); color: var(--ink); font-size: 20px; line-height: 1;
              cursor: pointer; box-shadow: 0 1px 4px var(--shadow); }}
.nav-backdrop {{ display: none; position: fixed; inset: 0; z-index: 90;
                background: rgba(15,30,50,.35); }}
@media (max-width: 1359px) {{
  .nav-toggle {{ display: block; }}
  .side-nav {{ transform: translateX(-100%); box-shadow: none; }}
  .side-nav.open {{ transform: translateX(0); box-shadow: 2px 0 8px var(--shadow); }}
  .nav-backdrop.show {{ display: block; }}
  .wrap {{ margin-left: 0 !important; padding-top: 56px; }}
}}
</style>
</head>
<body>
<button class="nav-toggle" aria-label="目录导航">&#9776;</button>
<div class="nav-backdrop"></div>
<nav class="side-nav">
<div class="nav-title">目录导航</div>
{nav_html}
</nav>
<div class="wrap">
<h1>{heading}</h1>
<div class="header-info">{meta}</div>
{content}
</div>
{script}
</body>
</html>"""


# ==========================================================================
# User jobs analyze report (per-user PEND/RUN/DONE/EXIT diagnosis).
# Mirrors the cluster report flow: collect → compute → dashboard → LLM.
# ==========================================================================
USER_REPORT_PREFIX = 'user_analyze_'

USER_ANALYZE_SYSTEM_PROMPT = """You are a senior LSF/OpenLava/Volclava HPC cluster operations expert generating a per-user jobs analysis report.

## Background

The user message provides two inputs:
- (A) Authoritative metrics already computed by the system: job counts by status, top pending reasons, exit-code distribution, RUN jobs' idle_factor distribution.
- (B) A raw snapshot of that user's jobs: bjobs -u <user> -w overview, and per-job details for RUN/PEND/EXIT.

The report's data section (作业概览) is ALREADY generated by the system — you MUST NOT output it again.

## Your job

Produce ONLY the analysis sections defined below (one per job status that has jobs, plus 集群问题反推 and 分析汇总). Base every number on input (A); treat it as ground truth and NEVER recompute or contradict it. In almost all cases write DIRECTLY from (A)+(B) WITHOUT calling any tool; only call a read-only tool (e.g. bjobs -l <jobid>) if a SPECIFIC fact you must cite is genuinely missing.

## Data usage rules

- Every number must be based on input (A); treat it as ground truth and NEVER recompute or contradict it.
- This is a **read-only analysis report**: do NOT execute any state-changing commands (bkill/badmin/bstop/bresume/brestart/bswitch/bmod, rm/kill/reboot, etc.) — they are auto-rejected.
- DO NOT analyze EDA license; never call query_license_info.

## Hard rules

1. **Evidence first**: only report a problem CONFIRMED by concrete data (a job id, an idle_factor value, an exit code, a pending reason). Never speculate. If a section has no confirmed issue, output exactly: <p>未发现明显问题。</p>. If a dimension's data is missing, state "数据不足" — do not speculate.
2. **RUN jobs judgment**: judge "slow/abnormal" by COMPREHENSIVELY weighing idle_factor (cputime/runtime; low = CPU mostly idle, likely waiting on IO/license/lock), run_time, cpu_time, max_mem vs requested mem, and slots. Do NOT use a single hard threshold — explain your reasoning. Each flagged job MUST cite its job id + concrete numbers.
3. **Executable solutions**: every "问题解决" must be a concrete, actionable directive and state explicitly whether the 用户 should act (e.g. resubmit with more mem/slots, fix command, kill hung job) or the 系统管理员 should act (e.g. queue config, host load, license shortage). When data supports a specific value, give it (e.g. "resubmit with -R rusage[mem=921600]", "bkill 12345"); when no concrete value can be derived, give a directional suggestion grounded in the observed pattern rather than a vague "优化资源". Never fabricate numbers you cannot tie to input (A)/(B). Commands/config go in <pre><code>…</code></pre>.
4. Reply in Chinese (中文).

## closed_Busy knowledge (common pitfall)

- closed_Busy is LSF automatic load control: hosts auto-close under high load and auto-reopen when resources free up — it **cannot** be recovered with `badmin hopen`.
- Only closed_Admin (manually closed by an admin) can be recovered with `badmin hopen`.
- In "系统管理员 TODO", do NOT suggest `badmin hopen` for closed_Busy hosts.

## Output format

Emit raw HTML fragment ONLY (no <html>/<body>, no markdown fences, no <h1>). Do NOT wrap section bodies in HTML comments. Do NOT repeat the 作业概览 data section.

DYNAMIC SECTIONS: the (B) snapshot lists jobs grouped by status. For EVERY status that has jobs, output ONE `<h2>{STATUS} 作业分析</h2>` section analyzing those jobs (RUN/PEND/EXIT get special treatment below; other states — DONE/PSUSP/USUSP/SSUSP/UNKWN/WAIT/ZOMBI/PROV etc. — get the generic analysis). Skip states with zero jobs entirely (do NOT emit an empty section for them).

Statuses are output in this order (only those that have jobs):
1. PEND → 2. RUN → 3. EXIT → 4. DONE → 5. PSUSP/USUSP/SSUSP → 6. UNKWN/WAIT/ZOMBI/PROV → 7. 集群问题反推 → 8. 分析汇总

Each problem follows the issue card spec: severity order fixed 严重 → 中等 → 轻微; card class issue high|mid|low (严重=high 红 / 中等=mid 橙 / 轻微=low 绿); four parts — 1) <span class="badge high|mid|low">严重|中等|轻微</span> + <b>问题标题</b>; 2) <p><b>问题描述：</b>…</p> (现象 + 数据依据，引用 job id/数值/pending reason/exit code); 3) <p><b>问题分析：</b>…</p> (根因); 4) <div><b>问题解决：</b>…</div> (谁处理 + 怎么做，命令/配置放 <pre><code>…</code></pre>).

<h2>PEND 作业分析</h2>  (only if PEND jobs exist)
先给汇总表：<table class="sortable"><thead><tr><th>Pending Reason</th><th>说明</th><th>作业数</th></tr></thead><tbody>…</tbody></table>，列出每种 pending reason + 简洁中文说明 + 作业数（按数量降序）。然后按数量从多到少，逐个分析每种 reason：用 issue 卡片说明该 reason 的含义、产生原因（用户自身如资源要太多/runlimit/依赖未满足，还是集群如队列满/主机 unavail/license 不足）、解决方案（用户或管理员具体怎么做）。

<h2>RUN 作业性能</h2>  (only if RUN jobs exist)
分两个子段（每段一个 <h3> 子标题 + 表）：
1. <h3>主机负载偏高</h3>：仅列出所在主机负载**明显偏高**的 RUN 作业（ut 高/r1m 高/mem 紧张），用 <table> 列 job_id/exec_host/host_ut/host_r1m/host_mem/idle_factor/runtime，并逐个 issue 卡片说明是否可能受影响。负载中等的不必列出。若无输出 <p>无。</p>。
2. <h3>运行超 14 天（疑似僵尸作业）</h3>：列出 runtime > 14 天的作业，用 <table> 列 job_id/command/runtime/started_time/exec_host，逐个 issue 卡片询问用户是否僵尸、是否需 kill（kill 命令放 <pre><code>bkill &lt;jobid&gt;</code></pre>）。若无输出 <p>无。</p>。表格中不要列 idle_factor/cpu_time/max_mem，但要列 command 让用户一眼看出是什么作业。
判断时必须引用 host_ut/host_r1m/host_mem/idle_factor/runtime 等具体数值。

<h2>EXIT 作业分析</h2>  (only if EXIT jobs exist)
先给汇总表：<table class="sortable"><thead><tr><th>Exit Code</th><th>Term Signal</th><th>说明</th><th>数量</th></tr></thead><tbody>…</tbody></table>，按 (exit_code, term_signal) 分组 + 简洁中文说明 + 数量（按数量降序）。然后逐个分析常见失败模式：OOM 被 kill（SIGTERM/SIGKILL，max_mem 高）、超时被 kill、命令错误（exit 1/2）、license 检出失败 等，每种一个 issue 卡片引用典型 job id + exit_code/term_signal + 判断依据。

<h2>DONE 作业分析</h2>  (only if DONE jobs exist)
DONE 表示正常完成。简要汇总完成数量、按 queue 分布，关注完成时长是否异常长（结合 started_time/finished_time 判断）。若无异常输出 <p>所有 DONE 作业正常完成，无异常。</p>。

<h2>{STATE} 作业分析</h2>  (for PSUSP/USUSP/SSUSP/UNKWN/WAIT/ZOMBI/PROV, only if that state has jobs)
说明该状态的含义（PSUSP=被管理员挂起 / USUSP=被用户挂起 / SSUSP=系统挂起 / UNKWN=未知 / WAIT=等待 / ZOMBI=僵尸 / PROV=准备中），为何作业处于此状态、是否需用户/管理员操作（如 resume/kill）。用 issue 卡片或列表呈现。

<h2>集群问题反推</h2>  (always output)
从该用户作业异常模式反推集群配置/负载层面问题（某队列长期排队=队列 slot 不足；多作业 OOM=主机内存配置/配额问题；多作业等 license=license 资源不足；多作业挤在高负载主机=调度或主机配置问题；多作业 SSUSP=系统资源紧张频繁挂起 等）。每个反推结论一张 issue 卡片，明确标注由<u>系统管理员</u>处理还是<u>用户</u>自查。若无反推结论输出 <p>未发现明显集群问题。</p>。

<h2>分析汇总</h2>
<!--
本段必须使用以下结构化卡片，不要写成长段落。每类卡片的 class 固定如下（用于按类型着色），不要改动 class：
  1) 一句整体结论：<div class="card-panel assess"><b>总体评估：</b>……（说明用户作业整体健康状况、最关键的一两个结论）</div>
  2) 严重问题清单：<div class="card-panel severe"><b>当前严重问题</b><ul><li>……</li></ul></div>（无则写"无"）
  3) 行动清单，左右两张卡片：
     <div class="two-col">
       <div class="panel admin"><div class="panel-h">系统管理员 TODO</div><ul><li>……</li></ul></div>
       <div class="panel user"><div class="panel-h">用户 TODO</div><ul><li>……</li></ul></div>
     </div>
语言精炼，每条 TODO 一句话、可执行。
-->
"""


def _collect_user_jobs_data(user, tool='lsf'):
    """Collect a single user's jobs snapshot in parallel.

    Returns a dict with keys: jobs_overview (bjobs -w), pending_reasons
    (bjobs -p aggregated), jobs_detail (bjobs -UF parsed by-job).
    """
    from concurrent.futures import ThreadPoolExecutor

    def _run(cmd):
        try:
            rc, out, err = common.run_command(cmd)

            return out.decode('utf-8', 'ignore') if out else ''
        except Exception:
            return ''

    user_q = shlex.quote(user)
    commands = {
        # -a = all job states (RUN/PEND/DONE/EXIT/SUSP); without it bjobs only
        # shows RUN/PEND, so EXIT/DONE jobs would be missed entirely.
        'overview': f'bjobs -u {user_q} -a -w',
        'pending': f'bjobs -u {user_q} -p',
        'detail_uf': f'bjobs -u {user_q} -a -UF',
    }

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {k: ex.submit(_run, c) for k, c in commands.items()}
        raw = {k: f.result(timeout=120) for k, f in futures.items()}

    # Parse the -UF output into a per-job dict via common_lsf.
    try:
        from common import common_lsf

        jobs_detail = common_lsf.get_bjobs_uf_info(commands['detail_uf'])
    except Exception:
        jobs_detail = {}

    # Collect host load (lsload) so RUN-job performance can blame the host.
    # get_lsload_info returns column-oriented {HOST_NAME:[...], ut:[...], ...};
    # transpose to {host: {ut, r1m, r15m, mem, status}} for O(1) lookup.
    host_load = {}

    try:
        lsload_dic = common_lsf.get_lsload_info('lsload -l')
        hosts = lsload_dic.get('HOST_NAME', [])

        for i, host in enumerate(hosts):
            host_load[host] = {
                'status': lsload_dic.get('status', [''] * len(hosts))[i] if i < len(lsload_dic.get('status', [])) else '',
                'ut': lsload_dic.get('ut', [''] * len(hosts))[i] if i < len(lsload_dic.get('ut', [])) else '',
                'r15s': lsload_dic.get('r15s', [''] * len(hosts))[i] if i < len(lsload_dic.get('r15s', [])) else '',
                'r1m': lsload_dic.get('r1m', [''] * len(hosts))[i] if i < len(lsload_dic.get('r1m', [])) else '',
                'r15m': lsload_dic.get('r15m', [''] * len(hosts))[i] if i < len(lsload_dic.get('r15m', [])) else '',
                'mem': lsload_dic.get('mem', [''] * len(hosts))[i] if i < len(lsload_dic.get('mem', [])) else '',
            }
    except Exception:
        pass

    return {
        'user': user,
        'overview_text': raw['overview'],
        'pending_reasons': _aggregate_pending_reasons(commands['pending'], top_n=8),
        'jobs_detail': jobs_detail,
        'host_load': host_load,
    }


def compute_user_metrics(user, raw_data):
    """Compute deterministic user-job metrics from the raw snapshot.

    Returns a dict with:
      - status_counts: {STATUS: count}
      - queue_summary: {queue: {RUN, PEND, DONE, EXIT, total}}
      - pending_reasons: [{'reason', 'count'}] from bjobs -p
      - run_jobs: list of RUN job dicts (cpu/idle/mem/slots/started/exec_host/host_load/runtime_seconds)
      - pend_jobs: list of PEND job dicts (queue/reasons/submitted/slots)
      - exit_jobs: list of EXIT job dicts (exit_code/term_signal/queue/command)
      - exit_summary: {(exit_code, term_signal): count}
      - long_run_jobs: RUN jobs with runtime > 14 days
      - idle_buckets: {bucket_label: count}
    No 'slow' verdict — that's the LLM's job; this only supplies objective numbers.
    """
    from collections import Counter
    import time as _time

    jobs_detail = raw_data.get('jobs_detail', {}) or {}
    host_load = raw_data.get('host_load', {}) or {}
    now_sec = _time.time()
    LONG_RUN_THRESHOLD = 14 * 24 * 3600

    status_counts = Counter()
    queue_summary = {}
    jobs_by_status = {}  # {STATUS: [job_dict, ...]} — dynamic, covers all states
    run_jobs = []
    pend_jobs = []
    exit_jobs = []
    exit_summary = Counter()
    exit_codes = Counter()
    term_signals = Counter()
    long_run_jobs = []

    def _bjobs_uf_to_seconds(t):
        """Parse 'Mon Oct 26 17:43:07' (no year) to epoch seconds.

        Reuses the year-prepending + future-rollback pattern from
        common_lsf.switch_bjobs_uf_time but returns seconds (int) or None.
        """
        if not t or t == 'N/A':
            return None

        parts = t.split()

        if len(parts) < 4:
            return None

        import datetime as _dt

        year = _dt.date.today().year

        try:
            sec = _time.mktime(_time.strptime(f'{year} {parts[1]} {parts[2]} {parts[3]}', '%Y %b %d %H:%M:%S'))
        except Exception:
            return None

        # If in the future, the job actually started last year.
        if sec > now_sec:
            try:
                sec = _time.mktime(_time.strptime(f'{year - 1} {parts[1]} {parts[2]} {parts[3]}', '%Y %b %d %H:%M:%S'))
            except Exception:
                return None

        return int(sec)

    def _queue_bucket(queue):
        q = queue or '(no queue)'

        if q not in queue_summary:
            queue_summary[q] = {'total': 0}

        return queue_summary[q]

    for job_id, info in jobs_detail.items():
        status = (info.get('status') or '').upper()
        queue = info.get('queue', '')
        status_counts[status] += 1
        bucket = _queue_bucket(queue)

        if status not in bucket:
            bucket[status] = 0

        bucket[status] += 1
        bucket['total'] += 1

        # Common fields every job gets (for the snapshot / LLM).
        job = {
            'job_id': job_id,
            'status': status,
            'queue': queue,
            'job_name': info.get('job_name', ''),
            'command': info.get('command', ''),
            'submitted_time': info.get('submitted_time', ''),
            'started_time': info.get('started_time', ''),
            'finished_time': info.get('finished_time', ''),
            'rusage_mem': info.get('rusage_mem', ''),
            'max_mem': info.get('max_mem', ''),
            'processors_requested': info.get('processors_requested', '1'),
            'pending_reasons': info.get('pending_reasons', []),
        }
        jobs_by_status.setdefault(status, []).append(job)

        # RUN-specific: exec host + load + runtime + idle factor.
        if status == 'RUN':
            started = info.get('started_time', '')
            runtime_sec = _bjobs_uf_to_seconds(started)
            runtime_s = (now_sec - runtime_sec) if runtime_sec else None
            exec_host = (info.get('started_on') or '').split()[0] if info.get('started_on') else ''
            job.update({
                'cpu_time': info.get('cpu_time', ''),
                'idle_factor': info.get('idle_factor', ''),
                'max_mem': info.get('max_mem', ''),
                'avg_mem': info.get('avg_mem', ''),
                'mem': info.get('mem', ''),
                'exec_host': exec_host,
                'host_load': host_load.get(exec_host, {}),
                'runtime_seconds': runtime_s,
            })
            run_jobs.append(job)

            if runtime_s is not None and runtime_s > LONG_RUN_THRESHOLD:
                long_run_jobs.append({
                    'job_id': job_id,
                    'queue': queue,
                    'exec_host': exec_host,
                    'runtime_seconds': runtime_s,
                    'started_time': started,
                    'job_name': info.get('job_name', ''),
                })
        elif status == 'PEND':
            pend_jobs.append(job)
        elif status == 'EXIT':
            ec = info.get('exit_code', '')
            ts = info.get('term_signal', '')
            exit_codes[ec or 'N/A'] += 1

            if ts:
                term_signals[ts] += 1

            exit_summary[(ec or 'N/A', ts or '')] += 1
            job.update({
                'exit_code': ec,
                'term_signal': ts,
                'max_mem': info.get('max_mem', ''),
            })
            exit_jobs.append(job)

    # idle_factor distribution buckets (objective reference only).
    idle_buckets = Counter()

    for j in run_jobs:
        ifact = j.get('idle_factor', '')

        try:
            v = float(ifact)

            if v < 0.5:
                idle_buckets['low(<0.5, CPU 大量空闲)'] += 1
            elif v < 0.8:
                idle_buckets['mid(0.5-0.8)'] += 1
            else:
                idle_buckets['high(>=0.8, CPU 繁忙)'] += 1
        except (ValueError, TypeError):
            idle_buckets['unknown'] += 1

    # ---- Job profile: macro-level characterization for someone who doesn't
    # know this user's workload (command distribution, resource stats, runtime
    # buckets). Built from already-collected data, no extra LSF calls.
    all_commands = Counter()

    for jobs in jobs_by_status.values():
        for j in jobs:
            cmd = j.get('command', '')

            if cmd:
                # Commands with >2 args differ per-job only by volatile args
                # (uuid/port/path), so aggregate by command name; short commands
                # (≤2 args) keep their args to stay distinct.
                tokens = cmd.split()

                if len(tokens) > 3:
                    agg_key = os.path.basename(tokens[0])
                else:
                    agg_key = cmd

                all_commands[agg_key] += 1

    total_jobs_for_profile = sum(all_commands.values())
    command_top = [{'command': cmd, 'count': cnt} for cmd, cnt in all_commands.most_common(5)]
    command_repeated = bool(command_top and command_top[0]['count'] / max(total_jobs_for_profile, 1) > 0.5)

    # Command conclusion: one-sentence summary.
    if not command_top:
        command_conclusion = '无作业数据。'
    elif command_repeated:
        top_cmd = command_top[0]['command']
        top_pct = round(command_top[0]['count'] * 100 / max(total_jobs_for_profile, 1))
        command_conclusion = f'用户作业高度重复，"{top_cmd}" 占比 {top_pct}%，属于批量提交的同类任务。'
    elif len(all_commands) == total_jobs_for_profile:
        command_conclusion = f'用户作业各不相同，{total_jobs_for_profile} 个作业使用了 {len(all_commands)} 种不同的 command，属于多样化任务。'
    else:
        command_conclusion = f'用户作业以少量 command 为主，{len(all_commands)} 种 command 覆盖 {total_jobs_for_profile} 个作业，存在一定重复。'

    # Resource stats from DONE jobs (completed jobs have full resource data).
    done_jobs = jobs_by_status.get('DONE', [])

    def _stats(values):
        nums = []

        for v in values:
            try:
                nums.append(float(v))
            except (ValueError, TypeError):
                pass

        if not nums:
            return {'min': None, 'avg': None, 'max': None}

        return {'min': min(nums), 'avg': round(sum(nums) / len(nums), 1), 'max': max(nums)}

    def _parse_mem_mb(s):
        if not s:
            return None

        try:
            return float(re.match(r'([\d.]+)', str(s)).group(1))
        except Exception:
            return None

    def _mb_to_gb(mb):
        if mb is None:
            return None

        return round(mb / 1024, 2)

    rusage_stats = _stats([_parse_mem_mb(j.get('rusage_mem', '')) for j in done_jobs])
    maxmem_stats = _stats([_parse_mem_mb(j.get('max_mem', '')) for j in done_jobs])
    slots_stats = _stats([j.get('processors_requested', '1') for j in done_jobs])

    # Convert MB → GB for display.
    resource_stats = {
        'rusage_mem_gb': {'min': _mb_to_gb(rusage_stats['min']), 'avg': _mb_to_gb(rusage_stats['avg']), 'max': _mb_to_gb(rusage_stats['max'])},
        'max_mem_gb': {'min': _mb_to_gb(maxmem_stats['min']), 'avg': _mb_to_gb(maxmem_stats['avg']), 'max': _mb_to_gb(maxmem_stats['max'])},
        'slots': slots_stats,
    }

    # Resource conclusion.
    if done_jobs and maxmem_stats['avg'] is not None:
        avg_slots = slots_stats['avg']
        avg_maxmem = _mb_to_gb(maxmem_stats['avg'])
        resource_conclusion = f'DONE 作业平均使用 {avg_slots:.0f} 个 slot、峰值内存 {avg_maxmem:.1f} GB。'
    else:
        resource_conclusion = '无 DONE 作业，无法统计资源用量。'

    # Runtime buckets from DONE jobs (started → finished).
    runtime_buckets = Counter()

    for j in done_jobs:
        st = j.get('started_time', '')
        ft = j.get('finished_time', '')

        if not st or not ft:
            continue

        # Parse "Mon Sep 4 09:00:00" → seconds (reuse _bjobs_uf_to_seconds).
        start_sec = _bjobs_uf_to_seconds(st)
        finish_sec = _bjobs_uf_to_seconds(ft)

        if start_sec is None or finish_sec is None:
            continue

        rt = finish_sec - start_sec

        if rt < 0:
            continue

        if rt < 3600:
            runtime_buckets['<1h'] += 1
        elif rt < 14400:
            runtime_buckets['1-4h'] += 1
        elif rt < 86400:
            runtime_buckets['4-24h'] += 1
        elif rt < 604800:
            runtime_buckets['1-7d'] += 1
        else:
            runtime_buckets['>7d'] += 1

    # Runtime conclusion.
    rt_total = sum(runtime_buckets.values())

    if rt_total > 0:
        top_bucket = max(runtime_buckets, key=runtime_buckets.get)
        top_pct = round(runtime_buckets[top_bucket] * 100 / rt_total)
        runtime_conclusion = f'DONE 作业运行时长集中在 "{top_bucket}" 区间（{top_pct}%），共 {rt_total} 个已完成作业。'
    else:
        runtime_conclusion = '无 DONE 作业运行时长数据。'

    profile = {
        'command_top': command_top,
        'command_repeated': command_repeated,
        'command_total': total_jobs_for_profile,
        'command_conclusion': command_conclusion,
        'resource_stats': resource_stats,
        'resource_conclusion': resource_conclusion,
        'runtime_buckets': dict(runtime_buckets),
        'runtime_conclusion': runtime_conclusion,
        'queue_count': len(queue_summary),
        'active_states': sorted(status_counts.keys()),
    }

    return {
        'user': user,
        'status_counts': dict(status_counts),
        'queue_summary': queue_summary,
        'total_jobs': sum(status_counts.values()),
        'pending_reasons': raw_data.get('pending_reasons', []),
        'jobs_by_status': jobs_by_status,
        'run_jobs': run_jobs,
        'pend_jobs': pend_jobs,
        'exit_jobs': exit_jobs,
        'exit_summary': dict(exit_summary),
        'long_run_jobs': long_run_jobs,
        'exit_codes': dict(exit_codes),
        'term_signals': dict(term_signals),
        'idle_buckets': dict(idle_buckets),
        'profile': profile,
    }


# Chinese explanations for common LSF pending reasons (best-effort mapping;
# unknown reasons show "—"). Used in the 作业概览 dashboard table.
_PEND_REASON_CN = {
    'Job slot requirement not satisfied': '无空闲 slot，资源不足',
    'Host load threshold exceeded': '主机负载超阈值',
    'Queue resource requirements not satisfied': '队列资源需求未满足',
    'Job requirements for resource not satisfied': '作业资源需求未满足',
    'Not enough hosts to run the job': '可用主机不足',
    "Queue's execution host requirements not satisfied": '队列执行主机需求未满足',
    "Job's requirements for execution host not satisfied": '执行主机需求未满足',
    'Memory requirements not satisfied': '内存需求未满足',
    'Jobslot limit reached': '已达作业 slot 上限',
    'Run limit exceeded': '运行时长超限',
    'License not available': 'License 不可用',
    'No eligible hosts': '无合格主机',
    'Job deadline has been reached': '作业截止时间已到',
    'Dependency not satisfied': '依赖未满足',
    'Waiting for dispatch condition': '等待调度条件',
    'Waiting for resource reservation': '等待资源预约',
    'Not enough slots to satisfy the job': '空闲 slot 不足',
}

# Chinese explanations for common (exit_code, term_signal) combos.
_EXIT_CODE_CN = {
    ('0', ''): '正常完成',
    ('1', ''): '通用错误（命令返回 1）',
    ('2', ''): '命令错误（参数/语法错误）',
    ('126', ''): '命令不可执行（权限不足）',
    ('127', ''): '命令未找到',
    ('130', 'TERM'): '被 Ctrl-C 终止',
    ('137', 'TERM'): '被 kill 终止（可能 OOM 或超时）',
    ('137', 'KILL'): '被 SIGKILL 强制终止（可能 OOM Killer）',
    ('139', 'SEGV'): '段错误（内存访问非法）',
    ('134', 'ABRT'): '程序异常中止（assert/abort）',
    ('136', 'FPE'): '浮点异常（除零等）',
    ('143', 'TERM'): '被 SIGTERM 正常终止',
    ('1', 'TERM'): '被终止且返回错误',
}


def render_user_dashboard(metrics):
    """Render the deterministic '作业概览' HTML section from user metrics.

    Mirrors the cluster report's visual style: banner verdict, card row,
    gauge bars, and sortable tables inside panels.
    """
    import html as _html

    def esc(value):
        return _html.escape(str(value))

    def num(value):
        return 'N/A' if value is None else str(value)

    def card(value, label, cls='', sub=''):
        klass = ('card ' + cls).strip()
        sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ''

        return (f'<div class="{klass}"><div class="val">{esc(value)}</div>'
                f'<div class="lab">{esc(label)}</div>{sub_html}</div>')

    def gauge(count, total, label, cls=''):
        if total == 0:
            width, text = 0, '0'
        else:
            width = round(count * 100 / total, 1)
            text = str(count)

        bar_cls = ('bar ' + cls).strip()

        return (f'<div class="bar-row"><span class="name">{esc(label)}</span>'
                f'<div class="{bar_cls}"><span style="width:{width}%"></span></div>'
                f'<span class="pct">{text}</span></div>')

    user = metrics.get('user', '')
    total = metrics.get('total_jobs', 0)
    sc = metrics.get('status_counts', {})
    run_n = sc.get('RUN', 0)
    pend_n = sc.get('PEND', 0)
    done_n = sc.get('DONE', 0)
    exit_n = sc.get('EXIT', 0)
    susp_n = sc.get('SSUSP', 0) + sc.get('USUSP', 0) + sc.get('PSUSP', 0)

    parts = []

    # ---- Banner: health verdict ----
    long_run = len(metrics.get('long_run_jobs', []))

    if exit_n > 0 or long_run > 0:
        banner_cls, verdict = 'bad', '用户作业存在异常，建议尽快处理'
    elif pend_n > 0 or susp_n > 0:
        banner_cls, verdict = 'warn', '用户作业存在需关注项'
    else:
        banner_cls, verdict = 'ok', '用户作业运行正常'

    meta_bits = [f'用户 {esc(user)}', f'{total} 个作业']

    if run_n:
        meta_bits.append(f'RUN {run_n}')

    if pend_n:
        meta_bits.append(f'PEND {pend_n}')

    if exit_n:
        meta_bits.append(f'EXIT {exit_n}')

    if long_run:
        meta_bits.append(f'超14天 {long_run}')

    parts.append(
        f'<div class="banner {banner_cls}"><div class="banner-v">{esc(verdict)}</div>'
        f'<div class="banner-m">{" · ".join(meta_bits)}</div></div>'
    )

    # ---- 作业画像 section: macro-level characterization ----
    prof = metrics.get('profile', {})

    parts.append('<h2 id="sec-profile">作业画像</h2>')

    # Command distribution table + conclusion.
    cmd_top = prof.get('command_top', [])

    if cmd_top:
        cmd_total = prof.get('command_total', 0)
        repeated = prof.get('command_repeated', False)
        badge = '<span class="badge warn">高度重复</span>' if repeated else '<span class="badge ok">各异</span>'
        head = '<tr><th>Command</th><th class="r">数量</th><th class="r">占比</th></tr>'
        body = ''

        for item in cmd_top:
            pct_val = round(item['count'] * 100 / max(cmd_total, 1), 1)
            full_cmd = esc(item['command'])
            # .cmd-cell: truncate long commands to one line with ellipsis (Excel-
            # like); title tooltip shows the full command, click toggles wrap.
            body += (f'<tr><td class="cmd-cell" title="{full_cmd}">{full_cmd}</td>'
                     f'<td class="r">{item["count"]}</td><td class="r">{pct_val}%</td></tr>')

        cmd_concl = prof.get('command_conclusion', '')
        concl_html = f'<p style="color:var(--ink);font-size:13px;margin:8px 0;">{esc(cmd_concl)}</p>' if cmd_concl else ''

        parts.append(
            f'<div class="panel"><div class="panel-h">Command 分布 {badge}</div>'
            '<table class="sortable"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>'
            + concl_html + '</div>'
        )

    # Resource stats cards (from DONE jobs, GB units, order: rusage/max_mem/slots).
    rs = prof.get('resource_stats', {})

    def _fmt_stat(stat, decimals=1):
        if stat is None or stat.get('min') is None:
            return 'N/A'

        return f"{stat['min']:.{decimals}f} / {stat['avg']:.{decimals}f} / {stat['max']:.{decimals}f}"

    rumem_stat = _fmt_stat(rs.get('rusage_mem_gb'), 2)
    mem_stat = _fmt_stat(rs.get('max_mem_gb'), 2)
    slot_stat = _fmt_stat(rs.get('slots'), 0)

    prof_cards = [
        card(rumem_stat, '申请内存 (GB)', sub='min / avg / max'),
        card(mem_stat, '峰值内存 (GB)', sub='min / avg / max'),
        card(slot_stat, 'Slots', sub='min / avg / max'),
    ]
    res_concl = prof.get('resource_conclusion', '')
    res_concl_html = f'<p style="color:var(--ink);font-size:13px;margin:8px 0;">{esc(res_concl)}</p>' if res_concl else ''

    parts.append(
        '<div class="panel"><div class="panel-h">资源用量统计（DONE 作业）</div><div class="cards">' + ''.join(prof_cards) + '</div>'
        + res_concl_html + '</div>'
    )

    # Runtime distribution gauge bars + conclusion.
    rt_buckets = prof.get('runtime_buckets', {})
    rt_total = sum(rt_buckets.values()) if rt_buckets else 0

    if rt_total > 0:
        rt_order = [
            ('<1h', '<1h', 'ok'),
            ('1-4h', '1-4h', 'ok'),
            ('4-24h', '4-24h', ''),
            ('1-7d', '1-7d', 'warn'),
            ('>7d', '>7d', 'bad'),
        ]
        bars = ''.join(
            gauge(rt_buckets.get(label, 0), rt_total, display, cls)
            for label, display, cls in rt_order
            if rt_buckets.get(label, 0) > 0
        )

        rt_concl = prof.get('runtime_conclusion', '')
        rt_concl_html = f'<p style="color:var(--ink);font-size:13px;margin:8px 0;">{esc(rt_concl)}</p>' if rt_concl else ''

        if bars:
            parts.append(
                '<div class="panel"><div class="panel-h">运行时长分布（DONE 作业）</div>' + bars + rt_concl_html + '</div>'
            )

    # ---- 作业概览 section ----
    parts.append('<h2 id="sec-overview">作业概览</h2>')

    # Card row: one card per status that has jobs (dynamic, covers all states).
    cards = [card(total, '作业总数', sub=f'用户 {esc(user)}')]

    status_card_order = [
        ('RUN', run_n, 'ok'),
        ('PEND', pend_n, 'warn'),
        ('DONE', done_n, ''),
        ('EXIT', exit_n, 'bad' if exit_n else ''),
        ('SSUSP', sc.get('SSUSP', 0), ''),
        ('USUSP', sc.get('USUSP', 0), ''),
        ('PSUSP', sc.get('PSUSP', 0), ''),
        ('UNKWN', sc.get('UNKWN', 0), ''),
        ('WAIT', sc.get('WAIT', 0), ''),
        ('ZOMBI', sc.get('ZOMBI', 0), ''),
        ('PROV', sc.get('PROV', 0), ''),
    ]

    for label, count, cls in status_card_order:
        if count > 0:
            cards.append(card(count, label, cls))

    parts.append('<div class="cards">' + ''.join(cards) + '</div>')

    # Status distribution gauge bars.
    status_order = [(label, c, cls) for label, c, cls in status_card_order if c > 0]
    gauges = ''.join(gauge(c, total, label, cls) for label, c, cls in status_order)

    if gauges:
        parts.append('<div class="panel"><div class="panel-h">作业状态分布</div>' + gauges + '</div>')

    # Per-queue breakdown table.
    qs = metrics.get('queue_summary', {})

    if qs:
        head = '<tr><th>队列</th><th class="r">PEND</th><th class="r">RUN</th><th class="r">DONE</th><th class="r">EXIT</th><th class="r">总计</th></tr>'
        body = ''

        for q, v in sorted(qs.items(), key=lambda kv: -kv[1].get('total', 0)):
            body += (f'<tr><td>{esc(q)}</td><td class="r">{v.get("PEND", 0)}</td>'
                     f'<td class="r">{v.get("RUN", 0)}</td><td class="r">{v.get("DONE", 0)}</td>'
                     f'<td class="r">{v.get("EXIT", 0)}</td><td class="r">{v.get("total", 0)}</td></tr>')

        parts.append(
            '<div class="panel"><div class="panel-h">按队列统计</div>'
            '<table class="sortable"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'
        )

    return ''.join(parts)


def _user_snapshot_text(raw_data, metrics):
    """Build the raw-snapshot text block fed to the LLM (input B).

    Uses the per-status job lists already organized by compute_user_metrics
    (run_jobs/pend_jobs/exit_jobs), capped to 50 each to bound prompt size.
    """
    user = raw_data.get('user', '')
    overview = raw_data.get('overview_text', '')

    def _fmt_run(j):
        rt = j.get('runtime_seconds')

        if rt is not None:
            days = rt // 86400
            hours = (rt % 86400) // 3600
            rt_str = f"{days}d{hours}h"
        else:
            rt_str = 'N/A'

        hl = j.get('host_load', {})

        return (
            f"  job {j['job_id']}: queue={j.get('queue', '')}, exec_host={j.get('exec_host', '')}, "
            f"cpu_time={j.get('cpu_time', '')}, idle_factor={j.get('idle_factor', '')}, "
            f"max_mem={j.get('max_mem', '')}, slots={j.get('processors_requested', '1')}, "
            f"runtime={rt_str} ({rt}s), "
            f"host_ut={hl.get('ut', '')}, host_r1m={hl.get('r1m', '')}, host_mem={hl.get('mem', '')}, "
            f"started={j.get('started_time', '')}, name={j.get('job_name', '')}, command={j.get('command', '')}"
        )

    def _fmt_pend(j):
        reasons = '; '.join(j.get('pending_reasons', []) or []) or '(no reason line)'

        return (
            f"  job {j['job_id']}: queue={j.get('queue', '')}, rusage_mem={j.get('rusage_mem', '')}, "
            f"slots={j.get('processors_requested', '1')}, submitted={j.get('submitted_time', '')}, "
            f"reasons={reasons}, name={j.get('job_name', '')}, command={j.get('command', '')}"
        )

    def _fmt_exit(j):
        return (
            f"  job {j['job_id']}: exit_code={j.get('exit_code', '')}, term_signal={j.get('term_signal', '')}, "
            f"queue={j.get('queue', '')}, max_mem={j.get('max_mem', '')}, started={j.get('started_time', '')}, "
            f"finished={j.get('finished_time', '')}, name={j.get('job_name', '')}, command={j.get('command', '')}"
        )

    run_lines = [_fmt_run(j) for j in metrics.get('run_jobs', [])[:50]]
    pend_lines = [_fmt_pend(j) for j in metrics.get('pend_jobs', [])[:50]]
    exit_lines = [_fmt_exit(j) for j in metrics.get('exit_jobs', [])[:50]]

    # Long-running jobs (> 14 days) — highlight for zombie detection.
    long_lines = []

    for j in metrics.get('long_run_jobs', []):
        rt = j.get('runtime_seconds', 0)
        days = rt // 86400

        long_lines.append(f"  job {j['job_id']}: queue={j['queue']}, exec_host={j.get('exec_host', '')}, runtime={days}天, started={j['started_time']}, name={j['job_name']}")

    # EXIT summary (exit_code, term_signal) → count.
    exit_summary_lines = []

    for (ec, ts), cnt in sorted(metrics.get('exit_summary', {}).items(), key=lambda x: -x[1]):
        exit_summary_lines.append(f"  exit_code={ec}, term_signal={ts or '(none)'}: {cnt} 个作业")

    # Other statuses (DONE/PSUSP/USUSP/SSUSP/UNKWN/WAIT/ZOMBI/PROV/...) — list
    # per-status job details so the LLM can analyze every state that has jobs.
    handled = {'RUN', 'PEND', 'EXIT', ''}
    other_sections = []

    for status, jobs in sorted(metrics.get('jobs_by_status', {}).items()):
        if status in handled:
            continue

        lines = []

        for j in jobs[:50]:
            lines.append(
                f"  job {j['job_id']}: queue={j.get('queue', '')}, submitted={j.get('submitted_time', '')}, "
                f"started={j.get('started_time', '')}, finished={j.get('finished_time', '')}, "
                f"reasons={'; '.join(j.get('pending_reasons', []) or []) or '(none)'}, "
                f"name={j.get('job_name', '')}, command={j.get('command', '')}"
            )

        other_sections.append(f"===== {status} 作业明细 (最多 50 条) =====\n" + '\n'.join(lines))

    other_block = ('\n\n'.join(other_sections) + '\n\n') if other_sections else ''

    return (
        f"用户：{user}\n\n"
        f"===== bjobs -u {user} -w (概览) =====\n{overview[:8000]}\n\n"
        "===== RUN 作业明细 (最多 50 条，含主机负载与 runtime) =====\n" + '\n'.join(run_lines) + "\n\n"
        "===== PEND 作业明细 (最多 50 条) =====\n" + '\n'.join(pend_lines) + "\n\n"
        "===== EXIT 作业明细 (最多 50 条) =====\n" + '\n'.join(exit_lines) + "\n\n"
        "===== EXIT 汇总 (exit_code, term_signal → 数量) =====\n" + '\n'.join(exit_summary_lines) + "\n\n"
        "===== 运行超过 14 天的作业（疑似僵尸） =====\n" + ('\n'.join(long_lines) if long_lines else '(无)') + "\n\n"
        + other_block
    )


def generate_user_jobs_report(api_base_url, api_key, model_name, user, tool='lsf',
                              db_path='', lmstat_path='lmstat', lmstat_bsub_command='',
                              doc_chunks=None, embedding_model='', embedding_api_base_url='',
                              embedding_api_key='', cluster='', debug=False,
                              on_thread_created=None):
    """
    Headless: collect a user's jobs snapshot, run the read-only agent loop, return
    the HTML report fragment. Mirrors generate_cluster_analyze_report.
    """
    from PyQt5.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        generate_user_jobs_report._app = QCoreApplication([])

    raw_data = _collect_user_jobs_data(user, tool=tool)
    metrics = compute_user_metrics(user, raw_data=raw_data)
    dashboard_html = render_user_dashboard(metrics)

    sc = metrics.get('status_counts', {})

    metrics_text = (
        "已由系统精确计算的权威指标（请直接引用，禁止重新估算或推翻）：\n"
        f"- 用户：{user}\n"
        f"- 作业总数：{metrics.get('total_jobs')}"
        f"（RUN={sc.get('RUN', 0)}, PEND={sc.get('PEND', 0)}, DONE={sc.get('DONE', 0)}, EXIT={sc.get('EXIT', 0)}）\n"
        "- PEND 原因 top："
        + '; '.join(f"{r['reason']}({r['count']})" for r in metrics.get('pending_reasons', []))
        + "\n"
        "- EXIT 退出码分布："
        + '; '.join(f"{k}={v}" for k, v in metrics.get('exit_codes', {}).items())
        + "\n"
        "- RUN 作业 idle_factor 分布："
        + '; '.join(f"{k}={v}" for k, v in metrics.get('idle_buckets', {}).items())
    )

    snapshot = _user_snapshot_text(raw_data, metrics)

    user_prompt = (
        "下面提供两部分输入：(A) 系统已精确计算的权威指标，(B) 该用户的作业原始快照。\n"
        "报告的“作业概览”段已由系统生成（含状态分布/排队原因/退出码/idle_factor 分布表），你不要重复输出。\n"
        "你只需基于以下数据，生成 PEND/RUN/EXIT/集群问题反推/分析汇总 五段（详见 system 指令的格式要求）。\n"
        "可调用只读命令下钻确认：bjobs -l <jobid> 看单作业细节，或调 bmonitor_cli 查历史趋势"
        "（如 bmonitor_cli db host-util <host> --days 7 看主机利用率趋势、bmonitor_cli db jobs --user "
        f"{user} --days 7 看历史完成作业、bmonitor_cli host-load <host> --days 7 看负载时序）。\n"
        "但不要执行任何改变集群状态的命令（bkill/bstop/bmod 等），也不要分析 license。\n\n"
        f"===== (A) 权威指标 =====\n{metrics_text}\n===== 指标结束 =====\n\n"
        f"===== (B) 用户作业快照 =====\n{snapshot}\n===== 快照结束 =====\n"
    )

    messages = [
        {"role": "system", "content": USER_ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # The dashboard (data section) is prepended; the LLM only writes the
    # analyze sections, so the final content = dashboard + LLM output.
    llm_content, _thread = _run_readonly_agent(
        messages,
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_name,
        db_path=db_path,
        lmstat_path=lmstat_path,
        lmstat_bsub_command=lmstat_bsub_command,
        doc_chunks=doc_chunks,
        embedding_model=embedding_model,
        embedding_api_base_url=embedding_api_base_url,
        embedding_api_key=embedding_api_key,
        debug=debug,
        on_thread_created=on_thread_created,
    )
    content = dashboard_html + '\n' + (llm_content or '')

    return content


# ==========================================================================
# Job analyze report (per-job deep diagnosis).
# Mirrors the cluster/user report flow: collect → compute → dashboard → LLM.
# ==========================================================================
JOB_REPORT_PREFIX = 'job_'


JOB_ANALYZE_SYSTEM_PROMPT = """You are a senior LSF/OpenLava/Volclava HPC cluster operations expert generating a single-job in-depth analysis report.

## Background

The user message provides:
- (A) Authoritative metrics — the job's full detail (status, queue, command, user, cpu_time, idle_factor, max_mem, started_time, finished_time, exit_code, term_signal, exec_host, pending_reasons, host_load) and mem/idle_factor time series if available.
- (B) 作业信息 and 机器信息 sections are ALREADY generated by the system — you MUST NOT output them again.

## Your job

Produce ONLY two sections — <h2>状态分析</h2> and <h2>分析汇总</h2> — analyzing the job based on its current status. Base every number on input (A); treat it as ground truth. You may call read-only tools (bjobs -l, lsload -l, bmonitor_cli) to drill down, but NEVER run state-changing commands.

## Data usage rules

- Every number must be based on input (A); treat it as ground truth and NEVER recompute or contradict it.
- This is a **read-only analysis report**: do NOT execute any state-changing commands (bkill/badmin/bstop/bresume/brestart/bswitch/bmod, rm/kill/reboot, etc.) — they are auto-rejected.
- NEVER execute `ssh`/`scp`/`sftp` (blocked at the tool level). If checking `dmesg` on the exec host is needed, tell the user to run it on that host — do NOT ssh yourself.
- DO NOT analyze EDA license; never call query_license_info. (If the job uses EDA license and you need to check it, use the query_license_info tool — not run_command.)

## Hard rules

1. **Evidence first**: only report findings CONFIRMED by concrete data. Never speculate. If nothing abnormal, output <p>未发现明显问题。</p>. If a dimension's data is missing, state "数据不足" — do not speculate.
2. **Executable solutions**: every "问题解决" must be a concrete, actionable directive, stating whether the 用户 or 系统管理员 should act. When data supports a specific value, give it (e.g. "resubmit with -R rusage[mem=921600]", "bkill 12345"); when no concrete value can be derived, give a directional suggestion grounded in the observed pattern rather than a vague "优化资源". Never fabricate numbers you cannot tie to input (A)/(B). Commands go in <pre><code>…</code></pre>.
3. **Objective description**: do not sensationalize. Only say "异常" or "严重" when data clearly proves it. Examples: runtime 4 days with RUNLIMIT 14 days — do NOT say "接近 RUNLIMIT"; low idle_factor only means CPU is idle — do NOT directly conclude "作业异常" or "严重异常状态". Use objective phrasing like "CPU 利用率低，可能原因包括..." rather than "严重异常".
4. **Distinguish fact from inference**: use "可能"/"疑似" for inferences; keep "数据证实的结论" separate from "推测".
5. Reply in Chinese (中文).

## Status-specific analysis

- **PEND**: analyze why the job is queuing (from pending_reasons). Is it user-side (too many resources requested, runlimit, dependency) or cluster-side (queue full, host unavail, license short)? Suggest how to speed up dispatch.
- **RUN**: analyze performance — is idle_factor low (CPU idle)? Is host load high (ut/r1m/mem tight)? Is memory near limit? If runtime > 14 days, mention it as "运行时间较长" (NOT "zombie" unless there's concrete evidence the job is stuck). If the job uses EDA license, use query_license_info to check. Each finding cites concrete numbers + job_id. Compare runtime to RUNLIMIT only if RUNLIMIT data is available, and only say "接近" when runtime > 80% of RUNLIMIT.
- **EXIT**: analyze failure — exit_code + term_signal pattern. TERM_EXTERNAL_SIGNAL 或类似信号表示作业被 LSF 外部信号终止，可能原因包括：(1) 操作系统 OOM killer（检查 max_mem 是否接近或超过主机内存）; (2) early_oom_killer 等第三方工具; (3) 其他系统进程发送信号。若 max_mem 较高，疑似 OOM。
  验证 OOM 疑似：调用 run_command 执行 `bmonitor_cli db host-load <exec_host> --days N`（exec_host 为作业执行主机，N 为 job 退出时间距今的天数，向上取整，如距今 2 天则 --days 2），查看 job 退出时间点附近的主机负载时序，重点关注 avail mem（可用内存）——如果 job 退出前 avail mem 很低（接近 0 或远低于正常水平），则很可能是 OOM 导致的。如需查内核 OOM 日志，提示用户在执行主机上执行 `dmesg | grep -i "oom" | tail -20`（AI 不直接 ssh）。引用 max_mem / exit_code / term_signal + 主机内存时序数据 + 推断依据。
  **主动查看错误日志**：作业信息中有 CWD 和 Command 字段。如果是应用层错误（exit code 1/2 等），应主动调用 run_command 查看作业的错误日志或输出文件，而不是让用户自己去查。常见做法：(1) 在 CWD 目录下找 .log/.err/.out 文件; (2) 用 `bjobs -l <jobid>` 查看 LSF 日志中的退出详情; (3) 如果 CWD 下有 vmanager session 日志，查看 debug_logs/ 目录下的 .err 文件。把查到的错误信息直接写进报告，不要只说"用户应检查错误日志"。
- **DONE**: brief confirmation that the job completed normally; mention runtime objectively (e.g. "运行 3 天完成") without judgment.
- **Other states** (SUSP/UNKWN/WAIT/ZOMBI/PROV): explain what the state means, why the job is in it, whether action is needed (resume/kill).

## closed_Busy knowledge (common pitfall)

- closed_Busy 是 LSF 自动行为：主机负载过高时 LSF 自动将主机状态设为 closed_Busy，停止接收新作业。这不是管理员手动关闭的，管理员也不能用 badmin hopen 恢复 closed_Busy 的主机——等服务器资源充足（负载降低）后会自动恢复 open。
- closed_Admin 才是管理员手动关闭的（可用 badmin hopen 恢复）。
- 在系统管理员 TODO 中，不要建议 badmin hopen closed_Busy 的主机，应说明"closed_Busy 是 LSF 负载自动管控，等资源释放后自动恢复"。

## LSF memory unit knowledge

- 本集群 LSF_UNIT_FOR_LIMITS = MB，所以 rusage[mem=] 的单位是 MB，不支持 G/T 后缀。
- 建议用户重新提交时，内存数值用 MB：如需 900GB 内存，写 -R "rusage[mem=921600]"（900 GB × 1024 = 921600 MB）。注意 GB → MB 是 ×1024，TB → MB 是 ×1024×1024。
- 不要写 rusage[mem=900G]，这种后缀在本集群不被识别。

## bsub -M knowledge (common pitfall)

- -M 是 LSF 的内存使用上限：作业使用内存超过 -M 值后，LSF 会自动 kill 该作业。
- 本集群**不推荐使用 -M**，因为它会限制内存上限，超过就被 kill，不利于灵活调度。
- 正确做法：只用 -R "rusage[mem=...]" 声明内存需求（给调度器参考，不会 kill），不设 -M 硬上限。
- 在建议用户重新提交时，**不要建议加 -M 参数**，只建议 -R "rusage[mem=...]"。

## Output format

Emit raw HTML fragment ONLY (no <html>/<body>, no markdown fences, no <h1>). Output EXACTLY these two sections:

<h2>状态分析</h2>
<!--
按严重度从高到低排列，分组顺序固定为 严重 → 中等 → 轻微。每个问题输出一张 issue 卡片，
卡片 class 用 issue high|mid|low（严重=high 红 / 中等=mid 橙 / 轻微=low 绿）。
每张卡片必须包含以下四部分：
  1) 标题行：<span class="badge high|mid|low">严重|中等|轻微</span> 后跟 <b>问题标题</b>
  2) <p><b>问题描述：</b>…</p>          —— 现象 + 数据依据（引用 (A) 中的精确数字/job id/idle_factor/exit_code 等）
  3) <p><b>问题分析：</b>…</p>          —— 根因
  4) <div><b>问题解决：</b>…</div>      —— 谁来解决（系统管理员/用户）+ 具体怎么做；命令/配置放 <pre><code>…</code></pre>
模板：
<div class="issue high">
  <p><span class="badge high">严重</span><b>问题标题</b></p>
  <p><b>问题描述：</b>……</p>
  <p><b>问题分析：</b>……</p>
  <div><b>问题解决：</b>由<u>系统管理员</u>处理：……<pre><code>…</code></pre></div>
</div>
若无任何确认的问题，本段仅输出 <p>未发现明显问题。</p>
-->

<h2>分析汇总</h2>
<!--
本段必须使用以下结构化卡片，不要写成长段落。每类卡片的 class 固定如下（用于按类型着色），不要改动 class：
  1) 一句整体结论：<div class="card-panel assess"><b>总体评估：</b>客观描述作业当前状态和关键指标，不下无依据的结论</div>
  2) 严重问题清单：<div class="card-panel severe"><b>当前严重问题</b><ul><li>……</li></ul></div>（无则写"无"）
  3) 行动清单，左右两张卡片：
     <div class="two-col">
       <div class="panel admin"><div class="panel-h">系统管理员 TODO</div><ul><li>……</li></ul></div>
       <div class="panel user"><div class="panel-h">用户 TODO</div><ul><li>……</li></ul></div>
     </div>
语言精炼，每条 TODO 一句话、可执行。
-->
"""


def _query_finished_job_detail(db_path, jobid):
    """Query a finished job's record from the historical job db (recent days).

    bjobs -UF only sees active jobs; DONE/EXIT jobs live in {db_path}/job/*.db.
    Returns a dict with the core fields (some bjobs-only fields are absent).
    """
    from common import common_sqlite3

    job_db_dir = os.path.join(str(db_path), 'job')

    if not os.path.isdir(job_db_dir):
        return {}

    key_list = ['job', 'job_name', 'user', 'status', 'queue', 'started_time',
                'finished_time', 'max_mem', 'avg_mem', 'rusage_mem', 'exit_code', 'command']

    db_files = sorted([f for f in os.listdir(job_db_dir) if f.endswith('.db')], reverse=True)

    for db_file in db_files:
        db_file_path = os.path.join(job_db_dir, db_file)

        try:
            data_dic = common_sqlite3.get_sql_table_data(
                db_file_path, '', 'job', key_list=key_list,
                select_condition='WHERE job=?', select_params=[str(jobid)])

            if data_dic and data_dic.get('job'):
                i = 0
                return {k: (data_dic.get(k, [''])[i] if i < len(data_dic.get(k, [])) else '') for k in key_list}
        except Exception:
            continue

    return {}


def _collect_job_data(jobid, tool='lsf', db_path=''):
    """Collect a single job's full snapshot: detail + mem curve + host load."""
    jobid_q = shlex.quote(str(jobid))

    # 1. Job detail via bjobs -UF (active jobs only).
    jobs_detail = {}

    try:
        from common import common_lsf

        jobs_detail = common_lsf.get_bjobs_uf_info(f'bjobs -UF -a {jobid_q}')
    except Exception:
        pass

    job_info = jobs_detail.get(str(jobid), {})

    if not job_info:
        for k, v in jobs_detail.items():
            job_info = v

            break

    # 2. Fallback: finished jobs (DONE/EXIT) are invisible to bjobs; query the
    # historical job db so the 作业信息 section still has core fields.
    if not job_info and db_path:
        job_info = _query_finished_job_detail(db_path, jobid)

    # 3. Mem/idle_factor time series via bmonitor_cli db job-mem.
    mem_curve = []

    try:
        rc, out, err = common.run_command(f'bmonitor_cli db job-mem {jobid_q} --days 7')

        if rc == 0 and out:
            import json as _json

            data = _json.loads(out.decode('utf-8', 'ignore'))

            if isinstance(data, list):
                mem_curve = data[:200]
    except Exception:
        pass

    # 4. Host load if the job is running (or was running).
    host_load = {}

    exec_host = (job_info.get('started_on') or '').split()[0] if job_info.get('started_on') else ''

    if exec_host:
        try:
            from common import common_lsf

            lsload_dic = common_lsf.get_lsload_info(f'lsload -l {shlex.quote(exec_host)}')
            hosts = lsload_dic.get('HOST_NAME', [])

            if hosts:
                i = 0
                host_load = {
                    'status': lsload_dic.get('status', [''] * len(hosts))[i] if i < len(lsload_dic.get('status', [])) else '',
                    'ut': lsload_dic.get('ut', [''] * len(hosts))[i] if i < len(lsload_dic.get('ut', [])) else '',
                    'r1m': lsload_dic.get('r1m', [''] * len(hosts))[i] if i < len(lsload_dic.get('r1m', [])) else '',
                    'r15m': lsload_dic.get('r15m', [''] * len(hosts))[i] if i < len(lsload_dic.get('r15m', [])) else '',
                    'mem': lsload_dic.get('mem', [''] * len(hosts))[i] if i < len(lsload_dic.get('mem', [])) else '',
                }
        except Exception:
            pass

    # Host config (ncpus / maxmem) via lshosts.
    host_config = {}

    if exec_host:
        try:
            from common import common_lsf

            lshosts_dic = common_lsf.get_lshosts_info(f'lshosts -w {shlex.quote(exec_host)}')
            hosts = lshosts_dic.get('HOST_NAME', [])

            if hosts:
                i = 0
                host_config = {
                    'ncpus': lshosts_dic.get('ncpus', [''] * len(hosts))[i] if i < len(lshosts_dic.get('ncpus', [])) else '',
                    'maxmem': lshosts_dic.get('maxmem', [''] * len(hosts))[i] if i < len(lshosts_dic.get('maxmem', [])) else '',
                    'maxswp': lshosts_dic.get('maxswp', [''] * len(hosts))[i] if i < len(lshosts_dic.get('maxswp', [])) else '',
                }
        except Exception:
            pass

    return {
        'jobid': str(jobid),
        'job_detail': job_info,
        'mem_curve': mem_curve,
        'host_load': host_load,
        'host_config': host_config,
        'exec_host': exec_host,
    }


def compute_job_metrics(jobid, raw_data):
    """Extract structured metrics from raw job data."""
    import time as _time

    detail = raw_data.get('job_detail', {}) or {}
    status = (detail.get('status') or '').upper()

    started_time = detail.get('started_time', '')
    finished_time = detail.get('finished_time', '')
    runtime_seconds = None

    def _parse_bjobs_time(t):
        """Parse 'Mon Sep 4 06:30:00' (no year) to epoch seconds or None."""
        if not t or t == 'N/A':
            return None

        parts = t.split()

        if len(parts) < 4:
            return None

        import datetime as _dt

        year = _dt.date.today().year

        try:
            sec = _time.mktime(_time.strptime(f'{year} {parts[1]} {parts[2]} {parts[3]}', '%Y %b %d %H:%M:%S'))

            if sec > _time.time():
                sec = _time.mktime(_time.strptime(f'{year - 1} {parts[1]} {parts[2]} {parts[3]}', '%Y %b %d %H:%M:%S'))

            return int(sec)
        except Exception:
            return None

    # Runtime: for RUN jobs = now - started; for EXIT/DONE = finished - started.
    start_sec = _parse_bjobs_time(started_time)

    if start_sec:
        if status == 'RUN':
            runtime_seconds = int(_time.time() - start_sec)
        else:
            finish_sec = _parse_bjobs_time(finished_time)

            if finish_sec:
                runtime_seconds = finish_sec - start_sec

    return {
        'jobid': str(jobid),
        'status': status,
        'queue': detail.get('queue', ''),
        'user': detail.get('user', ''),
        'command': detail.get('command', ''),
        'cwd': detail.get('cwd', ''),
        'job_name': detail.get('job_name', ''),
        'cpu_time': detail.get('cpu_time', ''),
        'idle_factor': detail.get('idle_factor', ''),
        'max_mem': detail.get('max_mem', ''),
        'avg_mem': detail.get('avg_mem', ''),
        'mem': detail.get('mem', ''),
        'rusage_mem': detail.get('rusage_mem', ''),
        'processors_requested': detail.get('processors_requested', '1'),
        'submitted_time': detail.get('submitted_time', ''),
        'started_time': started_time,
        'finished_time': detail.get('finished_time', ''),
        'exit_code': detail.get('exit_code', ''),
        'term_signal': detail.get('term_signal', ''),
        'exec_host': raw_data.get('exec_host', ''),
        'host_load': raw_data.get('host_load', {}),
        'host_config': raw_data.get('host_config', {}),
        'pending_reasons': detail.get('pending_reasons', []),
        'runtime_seconds': runtime_seconds,
        'mem_curve': raw_data.get('mem_curve', []),
    }


def render_job_dashboard(metrics):
    """Render the deterministic '作业信息' HTML section for a single job."""
    import html as _html

    def _parse_pct(s):
        """Parse '85%' → 85.0, return None on failure."""
        if not s:
            return None

        try:
            return float(re.match(r'([\d.]+)', str(s)).group(1))
        except Exception:
            return None

    def esc(value):
        return _html.escape(str(value))

    def card(value, label, cls='', sub=''):
        klass = ('card ' + cls).strip()
        sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ''

        return (f'<div class="{klass}"><div class="val">{esc(value)}</div>'
                f'<div class="lab">{esc(label)}</div>{sub_html}</div>')

    def _fmt_rt(seconds):
        """Format runtime: omit 0d, e.g. 3600 -> '1h', 90000 -> '1d1h'."""
        if seconds is None:
            return 'N/A'

        days = seconds // 86400
        hours = (seconds % 86400) // 3600

        if days > 0:
            return f'{days}d{hours}h'

        return f'{hours}h'

    status = metrics.get('status', 'UNKNOWN')
    jobid = metrics.get('jobid', '')
    queue = metrics.get('queue', '')
    user = metrics.get('user', '')
    exec_host = metrics.get('exec_host', '')
    rt = metrics.get('runtime_seconds')

    parts = []

    # ---- Banner ----
    if status == 'EXIT':
        banner_cls, verdict = 'bad', f'作业 {jobid} 已异常退出'
    elif status == 'PEND':
        banner_cls, verdict = 'warn', f'作业 {jobid} 正在排队'
    elif status == 'RUN':
        long_run = rt is not None and rt > 14 * 86400

        if long_run:
            banner_cls, verdict = 'warn', f'作业 {jobid} 运行超 14 天，疑似僵尸'
        else:
            banner_cls, verdict = 'ok', f'作业 {jobid} 正在运行'
    elif status == 'DONE':
        banner_cls, verdict = 'ok', f'作业 {jobid} 已正常完成'
    else:
        banner_cls, verdict = 'ok', f'作业 {jobid} 状态：{status}'

    meta_bits = [f'队列 {esc(queue)}', f'用户 {esc(user)}']

    if exec_host:
        meta_bits.append(f'主机 {esc(exec_host)}')

    if rt is not None:
        meta_bits.append(f'已运行 {_fmt_rt(rt)}')

    if metrics.get('exit_code'):
        meta_bits.append(f'exit={esc(metrics["exit_code"])}')

    parts.append(
        f'<div class="banner {banner_cls}"><div class="banner-v">{esc(verdict)}</div>'
        f'<div class="banner-m">{" · ".join(meta_bits)}</div></div>'
    )

    # ---- 作业信息 section ----
    parts.append('<h2 id="sec-info">作业信息</h2>')

    # Exec Host with host config (ncpus / maxmem) in parentheses.
    hc = metrics.get('host_config', {})
    host_desc = exec_host

    if hc and (hc.get('ncpus') or hc.get('maxmem')):
        bits = []

        if hc.get('ncpus'):
            bits.append(f"{hc['ncpus']} 核")

        if hc.get('maxmem'):
            bits.append(f"最大内存 {hc['maxmem']}")

        host_desc = f"{exec_host} ({', '.join(bits)})"

    # Detail table (no card row — the table already contains all fields).
    # Mem fields are in MB (from get_lsf_bjobs_uf_info), convert to GB for display.
    def _fmt_mem(mb_val):
        """Convert MB (float or str) to 'X.X GB', or '' if empty."""
        if not mb_val and mb_val != 0:
            return ''

        try:
            return f'{float(mb_val) / 1024:.1f} GB'
        except (ValueError, TypeError):
            return str(mb_val)

    detail_fields = [
        ('Job ID', jobid),
        ('Status', status),
        ('Queue', queue),
        ('User', user),
        ('Job Name', metrics.get('job_name', '')),
        ('CWD', metrics.get('cwd', '')),
        ('Command', metrics.get('command', '')),
        ('Exec Host', host_desc),
        ('Slots', metrics.get('processors_requested', '1')),
        ('CPU Time', metrics.get('cpu_time', '')),
        ('Idle Factor', metrics.get('idle_factor', '')),
        ('Requested Mem', _fmt_mem(metrics.get('rusage_mem', ''))),
        ('Current Mem', _fmt_mem(metrics.get('mem', ''))),
        ('Avg Mem', _fmt_mem(metrics.get('avg_mem', ''))),
        ('Max Mem', _fmt_mem(metrics.get('max_mem', ''))),
        ('Submitted', metrics.get('submitted_time', '')),
        ('Started', metrics.get('started_time', '')),
        ('Finished', metrics.get('finished_time', '')),
        ('Runtime', _fmt_rt(rt)),
        ('Exit Code', metrics.get('exit_code', '')),
        ('Term Signal', metrics.get('term_signal', '')),
    ]

    pr = metrics.get('pending_reasons', [])

    if pr:
        detail_fields.append(('Pending Reasons', '; '.join(pr)))

    body = ''.join(f'<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>' for label, value in detail_fields)
    parts.append(
        '<div class="panel"><div class="panel-h">详细信息</div>'
        '<table class="sortable"><thead><tr><th>字段</th><th>值</th></tr></thead>'
        '<tbody>' + body + '</tbody></table></div>'
    )

    # ---- mem/idle_factor curve (if available) ----
    curve = metrics.get('mem_curve', [])

    if curve:
        parts.append('<h2 id="sec-curve">mem/idle_factor 曲线</h2>')

        # Build runtime (minutes) / mem / idle_factor lists from the curve data.
        import datetime as _dt

        try:
            first_time = None
            runtime_list = []
            mem_list = []
            idle_list = []

            for point in curve:
                t_str = point.get('sample_time', '') or point.get('time', '')

                if not t_str:
                    continue

                try:
                    t_sec = _dt.datetime.strptime(str(t_str), '%Y%m%d_%H%M%S').timestamp()
                except Exception:
                    continue

                if first_time is None:
                    first_time = t_sec

                runtime_list.append(int((t_sec - first_time) / 60))

                m_val = point.get('mem', '') or point.get('mem_mb', '')

                if m_val:
                    try:
                        mem_list.append(float(m_val) / 1024)  # MB → GB
                    except Exception:
                        mem_list.append(None)
                else:
                    mem_list.append(None)

                i_val = point.get('idle_factor', '')

                if i_val != '' and i_val is not None:
                    try:
                        idle_list.append(round(float(i_val), 2))
                    except Exception:
                        idle_list.append(None)
                else:
                    idle_list.append(None)

            # Generate matplotlib charts and embed as base64 PNG.
            if runtime_list:
                import io as _io
                import base64 as _b64

                import matplotlib
                matplotlib.use('Agg')
                from matplotlib.figure import Figure

                # Memory curve.
                mem_chart = ''

                if any(m is not None for m in mem_list):
                    fig = Figure(figsize=(8, 3), dpi=100)
                    ax = fig.add_subplot(111)
                    valid_rt = [r for r, m in zip(runtime_list, mem_list) if m is not None]
                    valid_mem = [m for m in mem_list if m is not None]
                    ax.plot(valid_rt, valid_mem, color='#27ae60', linewidth=1.2, label='MEM')
                    ax.fill_between(valid_rt, valid_mem, color='#27ae60', alpha=0.15)
                    ax.legend(loc='upper right', frameon=False)
                    ax.set_title(f'memory usage for job "{jobid}"', fontsize=10)
                    ax.set_xlabel('Runtime (Minutes)', fontsize=9)
                    ax.set_ylabel('Memory Usage (GB)', fontsize=9)
                    ax.spines['top'].set_visible(False)
                    ax.spines['right'].set_visible(False)
                    ax.grid(True, axis='y', linestyle='--', linewidth=0.6, alpha=0.6)
                    buf = _io.BytesIO()
                    fig.savefig(buf, format='png', bbox_inches='tight', facecolor='white')
                    buf.seek(0)
                    mem_b64 = _b64.b64encode(buf.read()).decode()
                    mem_chart = f'<img src="data:image/png;base64,{mem_b64}" style="width:100%;max-width:800px;border:1px solid var(--line);border-radius:8px;margin:8px 0;" />'
                    buf.close()

                # Idle factor curve.
                idle_chart = ''

                if any(i is not None for i in idle_list):
                    fig2 = Figure(figsize=(8, 3), dpi=100)
                    ax2 = fig2.add_subplot(111)
                    valid_rt2 = [r for r, i in zip(runtime_list, idle_list) if i is not None]
                    valid_idle = [i for i in idle_list if i is not None]
                    ax2.plot(valid_rt2, valid_idle, color='#3498db', linewidth=1.2, label='IDLE_FACTOR')
                    ax2.fill_between(valid_rt2, valid_idle, color='#3498db', alpha=0.15)
                    ax2.legend(loc='upper right', frameon=False)
                    ax2.set_title(f'IDLE_FACTOR (cputime/runtime) for job "{jobid}"', fontsize=10)
                    ax2.set_xlabel('Runtime (Minutes)', fontsize=9)
                    ax2.set_ylabel('IDLE_FACTOR', fontsize=9)
                    ax2.spines['top'].set_visible(False)
                    ax2.spines['right'].set_visible(False)
                    ax2.grid(True, axis='y', linestyle='--', linewidth=0.6, alpha=0.6)
                    buf2 = _io.BytesIO()
                    fig2.savefig(buf2, format='png', bbox_inches='tight', facecolor='white')
                    buf2.seek(0)
                    idle_b64 = _b64.b64encode(buf2.read()).decode()
                    idle_chart = f'<img src="data:image/png;base64,{idle_b64}" style="width:100%;max-width:800px;border:1px solid var(--line);border-radius:8px;margin:8px 0;" />'
                    buf2.close()

                if mem_chart or idle_chart:
                    parts.append('<div class="panel"><div class="panel-h">内存与 idle_factor 曲线</div>' + mem_chart + idle_chart + '</div>')
                else:
                    parts.append('<div class="panel"><div class="panel-h">内存与 idle_factor 曲线</div><p>采样数据中无有效数值，无法绘制曲线。</p></div>')

        except Exception:
            # Fallback: show raw table.
            head = '<tr><th>采样时间</th><th class="r">mem</th><th class="r">idle_factor</th></tr>'
            body = ''

            for point in curve[:100]:
                t = point.get('sample_time', '') or point.get('time', '')
                m = point.get('mem', '') or point.get('mem_mb', '')
                i = point.get('idle_factor', '')
                body += f'<tr><td>{esc(t)}</td><td class="r">{esc(m)}</td><td class="r">{esc(i)}</td></tr>'

            parts.append(
                '<div class="panel"><div class="panel-h">内存与 idle_factor 时序</div>'
                '<table class="sortable"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'
            )

    return ''.join(parts)


def _job_snapshot_text(metrics):
    """Build the raw-snapshot text block fed to the LLM."""
    lines = [f"Job ID: {metrics.get('jobid', '')}"]

    for label, key in [('Status', 'status'), ('Queue', 'queue'), ('User', 'user'),
                       ('Command', 'command'), ('CPU Time', 'cpu_time'),
                       ('Idle Factor', 'idle_factor'), ('Max Mem', 'max_mem'),
                       ('Slots', 'processors_requested'), ('Started', 'started_time'),
                       ('Finished', 'finished_time'), ('Exit Code', 'exit_code'),
                       ('Term Signal', 'term_signal'), ('Exec Host', 'exec_host')]:
        val = metrics.get(key, '')

        if val:
            lines.append(f"  {label}: {val}")

    rt = metrics.get('runtime_seconds')

    if rt is not None:
        days = rt // 86400
        hours = (rt % 86400) // 3600
        rt_str = f'{days}d{hours}h' if days > 0 else f'{hours}h'
        lines.append(f'  Runtime: {rt_str} ({rt}s)')

    hl = metrics.get('host_load', {})

    if hl:
        lines.append(f"  Host Load: ut={hl.get('ut', '')}, r1m={hl.get('r1m', '')}, mem={hl.get('mem', '')}")

    pr = metrics.get('pending_reasons', [])

    if pr:
        lines.append(f"  Pending Reasons: {'; '.join(pr)}")

    curve = metrics.get('mem_curve', [])

    if curve:
        lines.append(f"  Mem/idle_factor time series ({len(curve)} points):")
        lines.append(f"    first: {curve[0] if curve else 'N/A'}")
        lines.append(f"    last:  {curve[-1] if curve else 'N/A'}")

    return '\n'.join(lines)


def generate_job_analyze_report(api_base_url, api_key, model_name, jobid, tool='lsf',
                                db_path='', lmstat_path='lmstat', lmstat_bsub_command='',
                                doc_chunks=None, embedding_model='', embedding_api_base_url='',
                                embedding_api_key='', cluster='', debug=False,
                                on_thread_created=None):
    """Headless: collect a job snapshot, run the read-only agent loop, return HTML."""
    from PyQt5.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        generate_job_analyze_report._app = QCoreApplication([])

    raw_data = _collect_job_data(jobid, tool=tool, db_path=db_path)
    metrics = compute_job_metrics(jobid, raw_data)
    dashboard_html = render_job_dashboard(metrics)

    snapshot = _job_snapshot_text(metrics)

    user_prompt = (
        f"===== (A) 作业详细信息 =====\n{snapshot}\n===== 信息结束 =====\n\n"
        f"===== mem/idle_factor 曲线数据(如有) =====\n"
        f"{'有 ' + str(len(metrics.get('mem_curve', []))) + ' 个采样点' if metrics.get('mem_curve') else '无采样数据'}\n"
    )

    messages = [
        {"role": "system", "content": JOB_ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    llm_content, _thread = _run_readonly_agent(
        messages,
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_name,
        db_path=db_path,
        lmstat_path=lmstat_path,
        lmstat_bsub_command=lmstat_bsub_command,
        doc_chunks=doc_chunks,
        embedding_model=embedding_model,
        embedding_api_base_url=embedding_api_base_url,
        embedding_api_key=embedding_api_key,
        debug=debug,
        on_thread_created=on_thread_created,
    )
    content = dashboard_html + '\n' + (llm_content or '')

    return content


# ==========================================================================
# Queue analyze report (per-queue load / resource-sufficiency diagnosis).
# Mirrors the cluster report flow: collect → compute → dashboard → LLM.
# ==========================================================================

QUEUE_ANALYZE_SYSTEM_PROMPT = """You are a senior LSF/OpenLava/Volclava HPC cluster operations expert generating a single-queue analysis report on short-term load and resource sufficiency.

## Background

The user message provides two inputs:
- (A) Authoritative metrics already computed by the system: queue status/prio/limits (MAX/JL_U/JL_P/JL_H/RUNLIMIT), slot total/used/idle, jobs RUN/PEND/SUSP, pend-rate, member-host state counts (open/closed_Admin/closed_Busy/closed_Full), top pending reasons.
- (B) A raw queue snapshot: bqueues -w row, bqueues -l detail (RUNLIMIT/USERS/HOSTS/RES_REQ/FAIRSHARE), member hosts status/load, bjobs -u all -q <queue> -w/-p.

The report's data sections (队列现状/队列限制配置/主机状态/排队原因) are ALREADY generated by the system — you MUST NOT output them again.

## Your job

Produce ONLY two sections — <h2>队列问题</h2> and <h2>分析汇总</h2> — covering two dimensions:

1. **Resource sufficiency**: is this queue's resource sufficient right now? For PEND jobs, classify the cause precisely:
   - **Limit factor (限制因素)**: queue/user/process/host slot limit, RUNLIMIT, user/host whitelist, RES_REQ — cite the specific limit value.
   - **Resource shortage (资源不足)**: idle slots ≈ 0, member hosts closed_Busy/closed_Full, high host load — cite host state / slot count.
   Then judge whether more machines are needed (only when resource shortage, not a limit).
2. **Load / submission problems**: given member-host load (closed_Busy/closed_Full counts, cpu%) and pending reasons, judge whether users' bsub requests are unreasonable — too many slots/mem, unreasonable span/select, too-long RUNLIMIT — and give a concrete corrected `bsub` example.

## Data usage rules

- Every number must be based on input (A); treat it as ground truth and NEVER recompute or contradict it.
- In almost all cases write DIRECTLY from (A)+(B) WITHOUT calling any tool; only call a read-only command (e.g. bqueues -l, bhosts -l) if a SPECIFIC fact you must cite is genuinely missing.
- This is a **read-only analysis report**: do NOT execute any state-changing commands (bkill/badmin/bstop/bresume/brestart/bswitch/bmod, rm/kill/reboot, etc.) — they are auto-rejected.
- DO NOT analyze EDA license; never call query_license_info.

## Hard rules

1. **Evidence first**: only report a problem CONFIRMED by concrete data (a limit value, a host state, a slot count, a cpu%). Never speculate. If a dimension's data is missing, state "数据不足" in the corresponding card — do not speculate.
2. **Classify PEND cause**: ALWAYS classify PEND as 限制因素 or 资源不足, citing the specific limit value / host state / slot count.
3. **Adding machines / raising limits**: ONLY recommend adding machines when slots_idle ≈ 0 AND pending jobs exist AND the cause is resource shortage (not a limit). If the cause is a limit, recommend raising the specific limit instead — do NOT recommend adding machines. When data supports a concrete value (e.g. raise JL/U 50→100, add ~200 slots), give it; otherwise give a directional suggestion grounded in the PEND pattern. Never fabricate numbers.
4. **Load problems**: base conclusions on host load numbers (closed_Busy/closed_Full counts, cpu%) and RES_REQ/RUNLIMIT; do NOT blame users without data. Give corrected bsub as <pre><code>bsub …</code></pre>.
5. **Executable solutions**: every "问题解决" must give a concrete, actionable directive and state explicitly whether the 系统管理员 or the 用户 should act. When data supports a specific value, give it (e.g. "raise JL/U 50→100", "resubmit with -R rusage[mem=921600]", "bsub -q normal -R ..."); when no concrete value can be derived, give a directional suggestion grounded in the observed pattern. Never fabricate numbers you cannot tie to input (A)/(B). Commands/config in <pre><code>…</code></pre>.
6. Reply in Chinese (中文).

## closed_Busy knowledge (common pitfall)

- closed_Busy is LSF automatic load control: hosts auto-close under high load and auto-reopen when resources free up — it **cannot** be recovered with `badmin hopen`.
- Only closed_Admin (manually closed by an admin) can be recovered with `badmin hopen`.
- In "系统管理员 TODO", do NOT suggest `badmin hopen` for closed_Busy member hosts.

## Output format

Emit raw HTML fragment ONLY (no <html>/<body>, no markdown fences, no <h1>). Output EXACTLY these two sections, in order:

<h2>队列问题</h2>
<!--
按严重度从高到低排列，分组顺序固定为 严重 → 中等 → 轻微。每个问题输出一张 issue 卡片，
卡片 class 用 issue high|mid|low（严重=high 红 / 中等=mid 橙 / 轻微=low 绿）。
每张卡片必须包含以下四部分：
  1) 标题行：<span class="badge high|mid|low">严重|中等|轻微</span> 后跟 <b>问题标题</b>
  2) <p><b>问题描述：</b>…</p>          —— 现象 + 数据依据（引用 (A) 中的精确数字/limit 值/主机状态/slot 数）
  3) <p><b>问题分析：</b>…</p>          —— 根因
  4) <div><b>问题解决：</b>…</div>      —— 谁来解决（系统管理员/用户）+ 具体怎么做；命令/配置放 <pre><code>…</code></pre>
重点覆盖：队列 slot 利用率、PEND 堆积根因（限制 vs 资源）、成员主机状态（closed_Busy/closed_Full）、是否需要加机器、用户 bsub 资源请求是否合理（负载问题）。
模板：
<div class="issue high">
  <p><span class="badge high">严重</span><b>问题标题</b></p>
  <p><b>问题描述：</b>……</p>
  <p><b>问题分析：</b>……</p>
  <div><b>问题解决：</b>由<u>系统管理员</u>处理：……<pre><code>…</code></pre></div>
</div>
若无任何确认的问题，本段仅输出 <p>未发现明显问题。</p>
-->

<h2>分析汇总</h2>
<!--
本段必须使用以下结构化卡片，不要写成长段落。每类卡片的 class 固定如下（用于按类型着色），不要改动 class：
  1) 一句整体结论：<div class="card-panel assess"><b>总体评估：</b>……（客观说明该队列当前资源是否充裕、PEND 根因（限制/资源）、短期是否需加机器）</div>
  2) 严重问题清单：<div class="card-panel severe"><b>当前严重问题</b><ul><li>……</li></ul></div>（无则写“无”）
  3) 行动清单，左右两张卡片：
     <div class="two-col">
       <div class="panel admin"><div class="panel-h">系统管理员 TODO</div><ul><li>……</li></ul></div>
       <div class="panel user"><div class="panel-h">用户 TODO</div><ul><li>……</li></ul></div>
     </div>
语言精炼，每条 TODO 一句话、可执行。
-->
"""


def _collect_queue_data(queue, tool='lsf'):
    """Collect one queue's snapshot (config/limits, member hosts, load, jobs, pend reasons)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from common import common_lsf

    queue_q = shlex.quote(queue)

    def _safe(key, func, *args, **kwargs):
        try:
            return key, func(*args, **kwargs)
        except Exception as error:
            return key, error

    tasks = [
        ('bqueues', common_lsf.get_bqueues_info),
        ('queue_host', common_lsf.get_queue_host_info),
        ('bhosts', common_lsf.get_bhosts_info),
        ('lsload', lambda: common_lsf.get_lsload_info(command='lsload -l' if tool == 'openlava' else 'lsload')),
        # bjobs 默认只查当前用户，须显式 -u all 才能看到全量 PEND/RUN 作业。
        ('pending_reasons', lambda: _aggregate_pending_reasons(f'bjobs -u all -q {queue_q} -p', top_n=8)),
        ('jobs_overview', lambda: _render_command_output(f'bjobs -u all -q {queue_q} -a -w', 120)),
        ('bqueues_l', lambda: _render_command_output(f'bqueues -l {queue_q}', 200)),
    ]

    results_dict = {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures_list = [executor.submit(_safe, key, func) for key, func in tasks]

        for future in as_completed(futures_list):
            key, value = future.result()
            results_dict[key] = value

    return results_dict


def compute_queue_metrics(queue, raw_data):
    """Compute exact per-queue metrics from the raw snapshot (mirrors compute_cluster_metrics)."""
    metrics = {
        'queue': queue,
        'status': '',
        'prio': '',
        'limit_max': '',
        'limit_jl_u': '',
        'limit_jl_p': '',
        'limit_jl_h': '',
        'runlimit': '',
        'users': '',
        'host_groups': '',
        'res_req': '',
        'scheduling_policies': '',
        'user_shares': '',
        'host_total': 0,
        'host_states': {'open': 0, 'closed_Admin': 0, 'closed_Busy': 0, 'closed_Full': 0, 'Others': 0},
        'slots_total': None,
        'slots_used': None,
        'slots_idle': None,
        'util_slot': None,
        'jobs_run': None,
        'jobs_pend': None,
        'jobs_susp': None,
        'pend_rate': None,
        'pending_reasons': [],
        'hosts': [],
    }

    # Queue config row + job counts (bqueues -w).
    bqueues_dict = raw_data.get('bqueues')

    if isinstance(bqueues_dict, Exception) or not bqueues_dict:
        bqueues_dict = {}

    queue_names_list = bqueues_dict.get('QUEUE_NAME', [])

    if queue in queue_names_list:
        idx = queue_names_list.index(queue)

        def cell(key):
            values_list = bqueues_dict.get(key, [])
            return values_list[idx] if idx < len(values_list) else ''

        metrics['status'] = cell('STATUS')
        metrics['prio'] = cell('PRIO')
        metrics['limit_max'] = cell('MAX')
        metrics['limit_jl_u'] = cell('JL/U')
        metrics['limit_jl_p'] = cell('JL/P')
        metrics['limit_jl_h'] = cell('JL/H')
        metrics['jobs_run'] = _lsf_int(cell('RUN'))
        metrics['jobs_pend'] = _lsf_int(cell('PEND'))
        metrics['jobs_susp'] = _lsf_int(cell('SUSP'))
        njobs = _lsf_int(cell('NJOBS')) or 0

        if njobs > 0:
            metrics['pend_rate'] = round((metrics['jobs_pend'] or 0) * 100 / njobs, 1)

    # Queue detail (bqueues -l <queue>): real scheduling constraints, e.g.
    # RUNLIMIT / USERS / HOSTS / RES_REQ / SCHEDULING POLICIES. bqueues -w's
    # MAX/JL_* columns are usually '-' (no slot limit), so these are the
    # constraints that actually gate dispatch.
    bqueues_l_text = raw_data.get('bqueues_l')

    if isinstance(bqueues_l_text, Exception) or not bqueues_l_text:
        bqueues_l_text = ''

    def _grab(pattern):
        match = re.search(pattern, bqueues_l_text, re.MULTILINE)
        return match.group(1).strip() if match else ''

    metrics['runlimit'] = _grab(r'RUNLIMIT\s*\n\s*([^\n]+)')
    metrics['users'] = _grab(r'^\s*USERS:\s*(.+)$')
    metrics['host_groups'] = _grab(r'^\s*HOSTS:\s*(.+)$')
    metrics['res_req'] = _grab(r'^\s*RES_REQ:\s*(.+)$')
    metrics['scheduling_policies'] = _grab(r'^\s*SCHEDULING POLICIES:\s*(.+)$')
    metrics['user_shares'] = _grab(r'^\s*USER_SHARES:\s*(.+)$')

    # Member hosts (queue_host + bhosts) → slot capacity + host states.
    queue_host_dict = raw_data.get('queue_host')

    if isinstance(queue_host_dict, Exception) or not queue_host_dict:
        queue_host_dict = {}

    bhosts_dict = raw_data.get('bhosts')

    if isinstance(bhosts_dict, Exception) or not bhosts_dict:
        bhosts_dict = {}

    member_hosts_list = queue_host_dict.get(queue, [])

    host_names_list = bhosts_dict.get('HOST_NAME', [])
    status_list = bhosts_dict.get('STATUS', [])
    max_list = bhosts_dict.get('MAX', [])
    njobs_list = bhosts_dict.get('NJOBS', [])
    host_index_dict = {name: i for i, name in enumerate(host_names_list)}

    slots_total = 0
    slots_used = 0

    for host in member_hosts_list:
        i = host_index_dict.get(host)

        if i is None:
            continue

        status = (status_list[i] if i < len(status_list) else '').strip()
        max_slots = _lsf_int(max_list[i] if i < len(max_list) else '') or 0
        njobs = _lsf_int(njobs_list[i] if i < len(njobs_list) else '') or 0

        if status == 'ok':
            metrics['host_states']['open'] += 1
        elif status.startswith('closed_Adm'):
            metrics['host_states']['closed_Admin'] += 1
        elif status.startswith('closed_Busy'):
            metrics['host_states']['closed_Busy'] += 1
        elif status.startswith('closed_Full'):
            metrics['host_states']['closed_Full'] += 1
        else:
            metrics['host_states']['Others'] += 1

        metrics['host_total'] += 1
        slots_total += max_slots
        slots_used += njobs
        metrics['hosts'].append({'name': host, 'status': status, 'max': max_slots, 'njobs': njobs})

    if member_hosts_list:
        metrics['slots_total'] = slots_total
        metrics['slots_used'] = slots_used
        metrics['slots_idle'] = slots_total - slots_used

        if slots_total > 0:
            metrics['util_slot'] = round(slots_used * 100 / slots_total, 1)

    # Member-host cpu load (lsload ut) for the resource-sufficiency judgement.
    lsload_dict = raw_data.get('lsload')

    if isinstance(lsload_dict, Exception) or not lsload_dict:
        lsload_dict = {}

    lsload_hosts_list = lsload_dict.get('HOST_NAME', [])
    ut_values_list = lsload_dict.get('ut', [])
    ut_dict = {}

    for i, host in enumerate(lsload_hosts_list):
        if i < len(ut_values_list) and re.match(r'^\d+%$', str(ut_values_list[i]).strip()):
            ut_dict[host] = int(str(ut_values_list[i]).strip().rstrip('%'))

    for host in metrics['hosts']:
        host['ut'] = ut_dict.get(host['name'])

    # ---- CPU utilization (lsload ut average across member hosts) ----
    def _parse_mem_mb(s):
        """Parse memory string like '1.9T' / '500G' / '2048M' → MB (float)."""
        if not s:
            return None

        try:
            m = re.match(r'([\d.]+)\s*([KMGT]?B?)(?:ytes)?', str(s), re.I)

            if not m:
                return None

            val = float(m.group(1))
            unit = (m.group(2) or 'M').upper()[:1]

            if unit == 'K':
                return val / 1024
            elif unit == 'G':
                return val * 1024
            elif unit == 'T':
                return val * 1024 * 1024

            return val  # MB
        except Exception:
            return None
    member_ut_values = [ut_dict.get(h['name']) for h in metrics['hosts'] if ut_dict.get(h['name']) is not None]

    if member_ut_values:
        metrics['util_cpu'] = round(sum(member_ut_values) / len(member_ut_values), 1)

    # ---- CPU cores total (lshosts ncpus for member hosts) ----
    lshosts_dict = raw_data.get('lshosts')

    if isinstance(lshosts_dict, Exception) or not lshosts_dict:
        lshosts_dict = {}

    lshosts_hosts_list = lshosts_dict.get('HOST_NAME', [])
    ncpus_list = lshosts_dict.get('ncpus', [])
    lshosts_index = {name: i for i, name in enumerate(lshosts_hosts_list)}
    cores_total = 0

    for host in metrics['hosts']:
        i = lshosts_index.get(host['name'])

        if i is not None and i < len(ncpus_list):
            cores_total += _lsf_int(ncpus_list[i]) or 0

    if cores_total > 0:
        metrics['cores_total'] = cores_total

    # ---- Memory total / used / idle (lsload mem + lshosts maxmem for member hosts) ----
    mem_list_lsload = lsload_dict.get('mem', [])
    mem_index = {name: i for i, name in enumerate(lsload_hosts_list)}
    maxmem_list = lshosts_dict.get('maxmem', [])
    total_max_mb = 0
    total_used_mb = 0

    for host in metrics['hosts']:
        # Total physical memory from lshosts.
        i = lshosts_index.get(host['name'])

        if i is not None and i < len(maxmem_list):
            total_max_mb += _parse_mem_mb(maxmem_list[i]) or 0

        # Used memory = total - available (lsload 'mem' = available).
        j = mem_index.get(host['name'])

        if j is not None and j < len(mem_list_lsload):
            avail = _parse_mem_mb(mem_list_lsload[j])

            if avail is not None:
                host_max = _parse_mem_mb(maxmem_list[i]) if (i is not None and i < len(maxmem_list)) else None

                if host_max:
                    total_used_mb += max(0, host_max - avail)

    if total_max_mb > 0:
        metrics['mem_total_gb'] = round(total_max_mb / 1024, 1)
        metrics['mem_used_gb'] = round(total_used_mb / 1024, 1)
        metrics['mem_idle_gb'] = round((total_max_mb - total_used_mb) / 1024, 1)
        metrics['util_mem'] = round(total_used_mb * 100 / total_max_mb, 1)

    # Pending reasons (bjobs -q <queue> -p).
    pending_reasons_list = raw_data.get('pending_reasons')

    if isinstance(pending_reasons_list, list):
        metrics['pending_reasons'] = pending_reasons_list

    return metrics


def render_queue_dashboard(metrics):
    """Render the deterministic queue data sections (banner + 队列资源 + 主机状态 + 排队原因)."""
    import html as _html

    def esc(value):
        return _html.escape(str(value))

    def num(value):
        return 'N/A' if value is None else str(value)

    def pct(value):
        return 'N/A' if value is None else f'{value}%'

    states = metrics.get('host_states', {})
    total = metrics.get('host_total', 0) or 0
    open_n = states.get('open', 0)
    closed_n = total - open_n
    parts = []

    # Overview banner: resource-sufficiency verdict.
    slots_idle = metrics.get('slots_idle')
    pend = metrics.get('jobs_pend') or 0
    util_slot = metrics.get('util_slot')

    if total == 0:
        banner_cls, verdict = 'warn', '无法获取队列主机数据'
    elif util_slot is not None and util_slot >= 90 and pend > 0:
        banner_cls, verdict = 'bad', '队列资源紧张，排队堆积，建议尽快处理'
    elif closed_n > 0 or pend > 0:
        banner_cls, verdict = 'warn', '队列存在需关注项'
    else:
        banner_cls, verdict = 'ok', '队列运行正常'

    meta_bits = [esc(metrics.get('queue', ''))]
    meta_bits.append(f"{total} 主机（open {open_n} / closed {closed_n}）")
    meta_bits.append(f"slot 利用率 {pct(util_slot)}")
    meta_bits.append(f"运行 {num(metrics.get('jobs_run'))} / 排队 {num(pend)}")
    parts.append(
        f'<div class="banner {banner_cls}"><div class="banner-v">{esc(verdict)}</div>'
        f'<div class="banner-m">{" · ".join(meta_bits)}</div></div>'
    )

    # 队列现状: basic info table.
    parts.append('<h2 id="sec-status">队列现状</h2>')

    def info_row(label, value):
        return f'<tr><td>{esc(label)}</td><td>{esc(value)}</td></tr>'

    pend_rate = metrics.get('pend_rate')
    pend_rate_text = f'{pend_rate}%' if pend_rate is not None else 'N/A'

    info_rows = [
        info_row('队列名', metrics.get('queue', '')),
        info_row('状态', metrics.get('status', '')),
        info_row('优先级', metrics.get('prio', '')),
        info_row('slots 总量', num(metrics.get('slots_total'))),
        info_row('slots 已用 / 空闲', f"{num(metrics.get('slots_used'))} / {num(slots_idle)}"),
        info_row('slot 利用率', pct(util_slot)),
        info_row('CPU 总核数', num(metrics.get('cores_total'))),
        info_row('CPU 利用率', pct(metrics.get('util_cpu'))),
        info_row('内存总量 (GB)', num(metrics.get('mem_total_gb'))),
        info_row('内存已用 / 空闲 (GB)', f"{num(metrics.get('mem_used_gb'))} / {num(metrics.get('mem_idle_gb'))}"),
        info_row('内存利用率', pct(metrics.get('util_mem'))),
        info_row('运行 / 排队 / 挂起', f"{num(metrics.get('jobs_run'))} / {num(pend)} / {num(metrics.get('jobs_susp'))}"),
        info_row('排队率', pend_rate_text),
    ]
    parts.append('<div class="panel"><div class="panel-h">队列基本信息</div>'
                 '<table class="sortable"><thead><tr><th>项目</th><th>值</th></tr></thead><tbody>'
                 + ''.join(info_rows) + '</tbody></table></div>')

    # 队列限制配置: real scheduling constraints from bqueues -l (bqueues -w 的
    # MAX/JL_* 列通常是 '-'，真正的约束在 RUNLIMIT/USERS/HOSTS/RES_REQ 等)。
    parts.append('<h2 id="sec-config">队列限制配置</h2>')

    constraint_rows = []

    if metrics.get('runlimit'):
        constraint_rows.append(info_row('RUNLIMIT(作业运行上限)', metrics['runlimit']))

    if metrics.get('scheduling_policies'):
        constraint_rows.append(info_row('调度策略', metrics['scheduling_policies']))

    if metrics.get('user_shares'):
        constraint_rows.append(info_row('用户份额', metrics['user_shares']))

    if metrics.get('users'):
        constraint_rows.append(info_row('允许用户', metrics['users']))

    if metrics.get('host_groups'):
        constraint_rows.append(info_row('主机组', metrics['host_groups']))

    if metrics.get('res_req'):
        constraint_rows.append(info_row('资源需求(RES_REQ)', metrics['res_req']))

    if constraint_rows:
        parts.append('<div class="panel"><div class="panel-h">队列限制配置</div>'
                     '<table class="sortable"><thead><tr><th>约束项</th><th>值</th></tr></thead><tbody>'
                     + ''.join(constraint_rows) + '</tbody></table></div>')
    else:
        parts.append('<p>未设置队列限制。</p>')

    # 主机状态: state bar chart + member-host detail table.
    parts.append('<h2 id="sec-hosts">主机状态</h2>')

    bar_order = [
        ('ok', open_n, 'ok'),
        ('closed_Admin', states.get('closed_Admin', 0), 'warn'),
        ('closed_Busy', states.get('closed_Busy', 0), ''),
        ('closed_Full', states.get('closed_Full', 0), 'bad'),
        ('Others', states.get('Others', 0), 'bad'),
    ]

    for name, count, cls in bar_order:
        width = round(count * 100 / total, 1) if total > 0 else 0
        bar_cls = ('bar ' + cls).strip()
        parts.append(
            f'<div class="bar-row"><span class="name">{esc(name)}</span>'
            f'<div class="{bar_cls}"><span style="width:{width}%"></span></div>'
            f'<span class="pct">{count} 台</span></div>'
        )

    hosts = metrics.get('hosts', [])

    if hosts:
        def metric_cell(value, threshold=90):
            if value is None:
                return '<td class="r">N/A</td>'

            if value >= threshold:
                return f'<td class="r"><span class="hot">{value}%</span></td>'

            return f'<td class="r">{value}%</td>'

        head = ('<tr><th>主机</th><th>状态</th><th class="r">slots总量</th>'
                '<th class="r">slots用量</th><th class="r">cpu%</th></tr>')
        rows = []

        for h in sorted(hosts, key=lambda x: (x['status'] == 'ok', x['name'])):
            abnormal = h['status'] != 'ok'
            status_cell = f'<span class="hot">{esc(h["status"])}</span>' if abnormal else esc(h['status'])
            rows.append(
                f'<tr><td>{esc(h["name"])}</td><td>{status_cell}</td>'
                f'<td class="r">{esc(h["max"])}</td><td class="r">{esc(h["njobs"])}</td>'
                f'{metric_cell(h.get("ut"))}</tr>'
            )

        parts.append(
            f'<details class="collapsible"><summary>展开成员主机明细（{len(hosts)} 台）</summary>'
            '<table class="sortable"><thead>' + head + '</thead><tbody>'
            + ''.join(rows) + '</tbody></table></details>'
        )

    # 排队原因.
    parts.append('<h2 id="sec-jobs">排队原因</h2>')

    reasons = metrics.get('pending_reasons', [])

    if reasons:
        rr = ['<table class="sortable"><thead><tr><th>排队原因</th><th class="r">作业数</th></tr></thead><tbody>']

        for item in reasons:
            rr.append(f'<tr><td>{esc(item["reason"])}</td><td class="r">{item["count"]}</td></tr>')

        rr.append('</tbody></table>')
        parts.append(''.join(rr))
    else:
        parts.append('<p>无排队作业。</p>')

    return '\n'.join(parts)


def _queue_snapshot_text(raw_data, metrics):
    """Render the raw queue snapshot (bqueues row + jobs overview) as plain text."""
    lines = [f"[Queue] {metrics.get('queue')}"]

    bqueues_dict = raw_data.get('bqueues')

    if isinstance(bqueues_dict, Exception) or not bqueues_dict:
        bqueues_dict = {}

    queue_name = metrics.get('queue')
    queue_names_list = bqueues_dict.get('QUEUE_NAME', [])

    if queue_name in queue_names_list:
        idx = queue_names_list.index(queue_name)

        for key in bqueues_dict:
            values_list = bqueues_dict[key]

            if idx < len(values_list):
                lines.append(f"  {key}: {values_list[idx]}")

    lines.append(f"[Jobs overview (bjobs -u all -q {queue_name} -a -w)]")
    overview = raw_data.get('jobs_overview')
    lines.append(overview if isinstance(overview, str) else '(no data)')

    lines.append(f"[Queue detail (bqueues -l {queue_name})]")
    bqueues_l_text = raw_data.get('bqueues_l')
    lines.append(bqueues_l_text if isinstance(bqueues_l_text, str) else '(no data)')

    return '\n'.join(lines)


def generate_queue_analyze_report(api_base_url, api_key, model_name, queue, tool='lsf',
                                  db_path='', lmstat_path='lmstat', lmstat_bsub_command='',
                                  doc_chunks=None, embedding_model='', embedding_api_base_url='',
                                  embedding_api_key='', cluster='', debug=False,
                                  on_thread_created=None):
    """Headless: collect one queue's snapshot, run the read-only agent loop, return HTML."""
    from PyQt5.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:
        generate_queue_analyze_report._app = QCoreApplication([])

    raw_data = _collect_queue_data(queue, tool=tool)
    metrics = compute_queue_metrics(queue, raw_data=raw_data)
    dashboard_html = render_queue_dashboard(metrics)

    states = metrics.get('host_states', {})

    metrics_text = (
        "已由系统精确计算的权威指标（请直接引用，禁止重新估算或推翻）：\n"
        f"- 队列：{queue}\n"
        f"- 资源：slots总={metrics.get('slots_total')}, 已用={metrics.get('slots_used')}, 空闲={metrics.get('slots_idle')}, 利用率={metrics.get('util_slot')}%\n"
        f"- 作业：运行={metrics.get('jobs_run')}, 排队={metrics.get('jobs_pend')}, 挂起={metrics.get('jobs_susp')}, 排队率={metrics.get('pend_rate')}%\n"
        f"- 成员主机：总={metrics.get('host_total')}, open={states.get('open')}, closed_Admin={states.get('closed_Admin')}, closed_Busy={states.get('closed_Busy')}, closed_Full={states.get('closed_Full')}, Others={states.get('Others')}\n"
        f"- 队列约束：RUNLIMIT={metrics.get('runlimit')}, 调度策略={metrics.get('scheduling_policies')}, 用户份额={metrics.get('user_shares')}, 允许用户={metrics.get('users')}, 主机组={metrics.get('host_groups')}, RES_REQ={metrics.get('res_req')}\n"
        "- 排队原因 top："
        + '; '.join(f"{r['reason']}({r['count']})" for r in metrics.get('pending_reasons', []))
    )

    snapshot = _queue_snapshot_text(raw_data, metrics)

    user_prompt = (
        "下面提供两部分输入：(A) 系统已精确计算的权威指标，(B) 该队列的原始快照。\n"
        "报告的“队列现状/队列限制配置/主机状态/排队原因”四段已由系统生成，你不要重复输出。\n"
        "你只需基于以下数据，生成“队列问题”和“分析汇总”两段（详见 system 指令的格式要求）。\n"
        "核心任务：\n"
        "1) 判断该队列短期资源是否充裕；若有排队，判断是限制因素（队列/用户/进程/主机 slot limit、RUNLIMIT、用户/主机白名单）还是资源不足（slots 空闲少、主机 closed_Busy/closed_Full、负载高）导致的；是否建议加机器。\n"
        "2) 研究负载问题：结合成员主机 closed_Busy 数量/负载与排队原因，分析用户 job 的 bsub 资源请求是否合理（如请求过多 slots/mem、span/select 不合理、RUNLIMIT 设置不当等），并给出具体纠正示例。\n"
        "可调用只读命令（如 bjobs -u all -q <queue> -l / bqueues -l <queue> / bhosts -l / lsload -l 等）下钻确认，"
        "但不要执行任何改变集群状态的命令，也不要分析 license。\n\n"
        f"===== (A) 权威指标 =====\n{metrics_text}\n===== 指标结束 =====\n\n"
        f"===== (B) 队列快照 =====\n{snapshot}\n===== 快照结束 =====\n"
    )

    messages = [
        {"role": "system", "content": QUEUE_ANALYZE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    llm_content, _thread = _run_readonly_agent(
        messages,
        api_base_url=api_base_url,
        api_key=api_key,
        model_name=model_name,
        db_path=db_path,
        lmstat_path=lmstat_path,
        lmstat_bsub_command=lmstat_bsub_command,
        doc_chunks=doc_chunks,
        embedding_model=embedding_model,
        embedding_api_base_url=embedding_api_base_url,
        embedding_api_key=embedding_api_key,
        debug=debug,
        on_thread_created=on_thread_created,
    )
    content = dashboard_html + '\n' + (llm_content or '')

    return content


class AnalyzeReportThread(QThread):
    """Background thread: generate an AI analyze HTML report and write it to output_file.

    Unified across report types (cluster/user/job/queue). report_type selects the
    generator + heading + meta label; target carries the cluster/user/jobid/queue value.
    """
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    _HEADING_DICT = {
        'cluster': '集群分析报告',
        'user': '用户作业分析报告',
        'job': '作业分析报告',
        'queue': '队列分析报告',
    }
    _META_LABEL_DICT = {
        'cluster': 'Cluster',
        'user': 'User',
        'job': 'Job',
        'queue': 'Queue',
    }

    def __init__(self, report_type, target, output_file, api_base_url, api_key,
                 model_name, tool='lsf', db_path='', lmstat_path='lmstat',
                 lmstat_bsub_command='', doc_chunks=None, embedding_model='',
                 embedding_api_base_url='', embedding_api_key='', cluster='',
                 debug=False):
        super().__init__()
        self.report_type = report_type
        self.target = target
        self.output_file = output_file
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.model_name = model_name
        self.tool = tool
        self.db_path = db_path
        self.lmstat_path = lmstat_path
        self.lmstat_bsub_command = lmstat_bsub_command
        self.doc_chunks = doc_chunks
        self.embedding_model = embedding_model
        self.embedding_api_base_url = embedding_api_base_url
        self.embedding_api_key = embedding_api_key
        self.cluster = cluster
        self.debug = debug
        self._inner_thread = None
        self._stop_requested = False

    def _set_inner_thread(self, thread):
        self._inner_thread = thread

        if self._stop_requested:
            thread.stop()

    def stop(self):
        self._stop_requested = True

        if self._inner_thread is not None:
            self._inner_thread.stop()

    def _generate(self):
        kwargs = dict(
            api_base_url=self.api_base_url,
            api_key=self.api_key,
            model_name=self.model_name,
            tool=self.tool,
            db_path=self.db_path,
            lmstat_path=self.lmstat_path,
            lmstat_bsub_command=self.lmstat_bsub_command,
            doc_chunks=self.doc_chunks,
            embedding_model=self.embedding_model,
            embedding_api_base_url=self.embedding_api_base_url,
            embedding_api_key=self.embedding_api_key,
            debug=self.debug,
            on_thread_created=self._set_inner_thread,
        )

        if self.report_type == 'cluster':
            return generate_cluster_analyze_report(**kwargs)

        kwargs['cluster'] = self.cluster

        if self.report_type == 'user':
            return generate_user_jobs_report(user=self.target, **kwargs)

        if self.report_type == 'job':
            return generate_job_analyze_report(jobid=self.target, **kwargs)

        return generate_queue_analyze_report(queue=self.target, **kwargs)

    def run(self):
        try:
            content = self._generate()

            if self._stop_requested:
                return

            if not content:
                self.error_signal.emit('LLM returned an empty report.')
                return

            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            meta = f'Generated: {timestamp}'

            if self.target:
                meta += f' | {self._META_LABEL_DICT[self.report_type]}: {self.target}'

            if self.cluster and self.report_type != 'cluster':
                meta += f' | Cluster: {self.cluster}'

            html = wrap_html_report(content, heading=self._HEADING_DICT[self.report_type], meta_line=meta)

            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(html)

            # 0o644: 报告 owner 写、其余用户只读。
            os.chmod(self.output_file, 0o644)

            self.finished_signal.emit(self.output_file)
        except Exception as error:
            if self._stop_requested:
                return

            self.error_signal.emit(str(error))
