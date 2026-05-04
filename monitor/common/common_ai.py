# -*- coding: utf-8 -*-

import os
import sys
import json
import datetime
import threading

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from common import common_license
from common import common_sqlite3

# openai and anthropic are lazy-imported inside their respective methods
# (_agent_loop_openai / _agent_loop_anthropic) to avoid ~4.8s startup penalty.
# They are only needed when the user actually starts an AI chat session.

# Default dangerous commands that require user confirmation.
DEFAULT_DANGEROUS_COMMANDS = ['bkill', 'badmin', 'brestart', 'bstop', 'bresume', 'bswitch']

SYSTEM_PROMPT = """You are an AI assistant for LSF/OpenLava/Volclava HPC cluster management, integrated into the lsfMonitor tool.

You can help users with:
1. Querying and managing LSF jobs, hosts, queues, and users with run_command tool.
2. Querying EDA license usage information with query_license_info tool.
3. Looking up historical job records from the database with query_job_history tool.
4. Searching local documentation (user manuals, guides) with search_documentation tool.

Guidelines:
- When users ask about cluster status, jobs, hosts, or queues, use run_command to execute the appropriate LSF command.
- When users ask about EDA license usage, use query_license_info.
- When users ask about past/finished jobs, use query_job_history.
- When users ask about command syntax, options, configuration, or best practices, use search_documentation to look up the answer first.
- Explain the results clearly and concisely.
- If a command fails, explain what went wrong and suggest alternatives.
- Respond in the same language the user uses.
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


# ============================================================
# Tool execution functions (shared by both API types).
# ============================================================

def execute_command(command, forbidden_list, dangerous_list, confirm_callback):
    """Execute a command with safety checks."""
    if not command.strip():
        return "Error: empty command."

    base_cmd = command.strip().split()[0]

    for forbidden in forbidden_list:
        if base_cmd == forbidden:
            return f"Error: command '{base_cmd}' is forbidden by configuration."

    for dangerous in dangerous_list:
        if base_cmd == dangerous:
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
            output = output[:MAX_OUTPUT_LENGTH] + f"\n... (truncated, total {len(output)} chars)"

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

        if job_id:
            conditions.append(f"job='{job_id}'")

        if user:
            conditions.append(f"user='{user}'")

        if queue:
            conditions.append(f"queue='{queue}'")

        if status:
            conditions.append(f"status='{status}'")

        select_condition = ''

        if conditions:
            select_condition = 'WHERE ' + ' AND '.join(conditions)

        if limit:
            select_condition += f" LIMIT {limit}"

        key_list = ['job', 'job_name', 'user', 'status', 'queue', 'started_time', 'finished_time', 'max_mem', 'avg_mem', 'rusage_mem', 'exit_code', 'command']
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', 'job', key_list=key_list, select_condition=select_condition)

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
    Load documents from db/ai/ directory.
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
        return ("No documentation loaded. Place RAG files (rag_chunks.json + rag_faiss.index) in the db/ai/ directory.", [])

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
# Skill loading (conf/skills/*/SKILL.md).
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
            if tag in msg_lower:
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
                 forbidden_commands=None, dangerous_commands=None, doc_chunks=None, skills=None,
                 embedding_model='', embedding_api_base_url='', embedding_api_key='',
                 experience_cases=None):
        super().__init__()
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.messages = messages
        self.db_path = db_path
        self.license_dic = license_dic or {}
        self.lmstat_path = lmstat_path
        self.lmstat_bsub_command = lmstat_bsub_command
        self.forbidden_commands = forbidden_commands or []
        self.dangerous_commands = dangerous_commands or DEFAULT_DANGEROUS_COMMANDS
        self.doc_chunks = doc_chunks or []
        self.skills = skills or []
        self.embedding_model = embedding_model
        self.embedding_api_base_url = embedding_api_base_url.rstrip('/') if embedding_api_base_url else self.api_base_url
        self.embedding_api_key = embedding_api_key if embedding_api_key else self.api_key
        self.experience_cases = experience_cases or []
        self._stop_flag = False
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._sources = {"rag_sources": [], "skills": []}

        # Auto-detect API type.
        self.api_type = detect_api_type(model_name)

        # Copy the system message dict so skill/experience injection doesn't
        # accumulate across conversations (messages list is shared by reference).
        if self.messages and self.messages[0].get('role') == 'system':
            self.messages[0] = dict(self.messages[0])

        # Inject matched skill content into system prompt for this conversation.
        self._inject_skills()

        # Inject similar solved cases as experience references.
        self._inject_experience()

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

    def _inject_experience(self):
        """Inject similar solved cases into system prompt as reference experience."""
        if not self.experience_cases:
            return

        if not self.messages or self.messages[0].get('role') != 'system':
            return

        lines = ['\n## 历史参考案例', '以下是之前成功解决的相似问题，请参考：']

        for i, case in enumerate(self.experience_cases, 1):
            lines.append(f'\n### 案例 {i}')
            lines.append(f'**用户问题：** {case.get("question", "")}')
            lines.append(f'**解决方案：** {case.get("answer", "")[:500]}')

            tool_calls = case.get('tool_calls', '[]')

            if tool_calls and tool_calls != '[]':
                try:
                    tools = json.loads(tool_calls) if isinstance(tool_calls, str) else tool_calls
                    tool_names = [t.get('name', '') for t in tools if isinstance(t, dict)]

                    if tool_names:
                        lines.append(f'**使用的工具：** {", ".join(tool_names)}')
                except (json.JSONDecodeError, TypeError):
                    pass

        self.messages[0]['content'] = self.messages[0]['content'] + '\n'.join(lines)

    def stop(self):
        self._stop_flag = True

    def set_confirm_result(self, result):
        """Called from main thread to respond to confirmation request."""
        self._confirm_result = result
        self._confirm_event.set()

    def _request_confirmation(self, command):
        """Request user confirmation for dangerous command. Blocks until user responds."""
        self._confirm_result = False
        self._confirm_event.clear()
        self.confirm_requested.emit(command)
        self._confirm_event.wait()
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
                self.forbidden_commands,
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

        if not any(f'/v{n}' in base_url for n in range(1, 10)):
            base_url = base_url + '/v1'

        cache_key = (base_url, self.api_key)

        if cache_key not in AiChatThread._openai_client_cache:
            AiChatThread._openai_client_cache[cache_key] = OpenAI(base_url=base_url, api_key=self.api_key)

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

        for loop_i in range(10):
            if self._stop_flag:
                return

            # Debug: log prompt size.
            _sys_len = len(self.messages[0].get('content', '')) if self.messages else 0
            _total_chars = sum(len(str(m.get('content', ''))) for m in self.messages)
            _msg_count = len(self.messages)
            common.bprint(f'[AI Debug] Loop {loop_i}: {_msg_count} messages, system_prompt={_sys_len} chars, total={_total_chars} chars', date_format='%Y-%m-%d %H:%M:%S')

            self.status_signal.emit('Waiting for LLM response')

            _t_api = _time.time()

            try:
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=self.messages,
                    tools=TOOLS_OPENAI,
                    stream=True
                )
            except Exception as e:
                self.error_signal.emit(f"API call failed: {e}")
                return

            common.bprint(f'[AI Debug] create() returned: {_time.time() - _t_api:.2f}s', date_format='%Y-%m-%d %H:%M:%S')

            full_content = ""
            tool_calls_data = {}
            _first_chunk = True

            try:
                for chunk in response:
                    if _first_chunk:
                        common.bprint(f'[AI Debug] First chunk received: {_time.time() - _t_api:.2f}s', date_format='%Y-%m-%d %H:%M:%S')
                        _first_chunk = False

                    if self._stop_flag:
                        return

                    choice = chunk.choices[0] if chunk.choices else None

                    if not choice:
                        continue

                    delta = choice.delta

                    if delta and delta.content:
                        full_content += delta.content
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
            except Exception as e:
                self.error_signal.emit(f"Stream error: {e}")
                return

            common.bprint(f'[AI Debug] Stream done: {_time.time() - _t_api:.2f}s, content={len(full_content)} chars, tool_calls={len(tool_calls_data)}', date_format='%Y-%m-%d %H:%M:%S')

            if not tool_calls_data:
                if full_content:
                    self.messages.append({"role": "assistant", "content": full_content})

                return

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
            common.bprint(f'[AI Debug] anthropic client ready: {_time.time() - _t_start:.2f}s', date_format='%Y-%m-%d %H:%M:%S')
        except ImportError:
            self.error_signal.emit('anthropic package is not installed. Run: pip install anthropic')
            return

        for loop_i in range(10):
            if self._stop_flag:
                return

            # Debug: log prompt size.
            _sys_len = len(self.messages[0].get('content', '')) if self.messages else 0
            _total_chars = sum(len(str(m.get('content', ''))) for m in self.messages)
            _msg_count = len(self.messages)
            common.bprint(f'[AI Debug] Loop {loop_i}: {_msg_count} messages, system_prompt={_sys_len} chars, total={_total_chars} chars', date_format='%Y-%m-%d %H:%M:%S')

            self.status_signal.emit('Waiting for LLM response')

            # Convert messages to Anthropic format.
            system, anthropic_msgs = openai_messages_to_anthropic(self.messages)

            _t_api = _time.time()

            try:
                stream = client.messages.create(
                    model=self.model_name,
                    system=system,
                    messages=anthropic_msgs,
                    tools=TOOLS_ANTHROPIC,
                    max_tokens=4096,
                    stream=True
                )
            except Exception as e:
                self.error_signal.emit(f"API call failed: {e}")
                return

            common.bprint(f'[AI Debug] create() returned: {_time.time() - _t_api:.2f}s', date_format='%Y-%m-%d %H:%M:%S')

            full_content = ""
            tool_calls = {}  # {block_index: {id, name, arguments}}
            _first_chunk = True

            try:
                for event in stream:
                    if self._stop_flag:
                        return

                    if _first_chunk:
                        common.bprint(f'[AI Debug] First chunk received: {_time.time() - _t_api:.2f}s', date_format='%Y-%m-%d %H:%M:%S')
                        _first_chunk = False

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
                            self.token_received.emit(event.delta.text)
                        elif event.delta.type == 'input_json_delta':
                            if event.index in tool_calls:
                                tool_calls[event.index]['arguments'] += event.delta.partial_json
            except Exception as e:
                self.error_signal.emit(f"Stream error: {e}")
                return

            common.bprint(f'[AI Debug] Stream done: {_time.time() - _t_api:.2f}s, content={len(full_content)} chars, tool_calls={len(tool_calls)}', date_format='%Y-%m-%d %H:%M:%S')

            # No tool calls -> done.
            if not tool_calls:
                if full_content:
                    self.messages.append({"role": "assistant", "content": full_content})

                return

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
                common.bprint(f'[AI Debug] Tool "{tool_name}" executed: {_time.time() - _t_tool:.2f}s, result={len(result)} chars', date_format='%Y-%m-%d %H:%M:%S')
                self.tool_call_result.emit(tool_name, result)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc['id'],
                    "content": result
                })

            # Loop back to let LLM process tool results.
