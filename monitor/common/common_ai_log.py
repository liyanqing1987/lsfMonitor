# -*- coding: utf-8 -*-
#
# common_ai_log.py
#
# Author: liyanqing.1987
# Created: 2026-04-30
# Description: AI conversation log database operations and report generation.

import os
import re
import sys
import json
import uuid
import datetime

from PyQt5.QtCore import QThread, pyqtSignal

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common_sqlite3

# Column definitions (shared across all per-user tables).
TABLE_KEY_LIST = ['session_id', 'timestamp', 'user', 'cluster', 'host', 'question', 'answer', 'tool_calls', 'resolution', 'keywords']
TABLE_KEY_TYPE_LIST = ['TEXT PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']

# Insights table: distilled knowledge from solved conversations.
INSIGHTS_TABLE = 'insights'
INSIGHTS_KEY_LIST = ['id', 'timestamp', 'session_id', 'insight', 'keywords', 'source_question']
INSIGHTS_KEY_TYPE_LIST = ['TEXT PRIMARY KEY', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT']

# Chinese/English stop words for keyword extraction.
STOP_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
    'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'between', 'out', 'off', 'over',
    'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when',
    'where', 'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more',
    'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
    'same', 'so', 'than', 'too', 'very', 'just', 'because', 'but', 'and',
    'or', 'if', 'while', 'about', 'up', 'down', 'it', 'its', 'this',
    'that', 'these', 'those', 'i', 'me', 'my', 'we', 'our', 'you', 'your',
    'he', 'him', 'his', 'she', 'her', 'they', 'them', 'their', 'what',
    'which', 'who', 'whom',
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会',
    '着', '没有', '看', '好', '自己', '这', '他', '吗', '那', '她',
    '请', '怎么', '什么', '如何', '为什么', '可以', '能', '吧', '呢',
}


def gen_table_name(user):
    """Generate per-user table name."""
    return f'conversations_{user}'


def gen_session_id():
    """Generate an 8-character UUID for session identification."""
    return uuid.uuid4().hex[:8]


def init_ai_log_db(db_path):
    """
    Initialize the AI log database directory.
    Tables are created per-user on first save.
    Returns the db_file path.
    If the shared db_path is not writable, falls back to ~/.lsfMonitor/db/ai/.
    """
    ai_db_dir = os.path.join(str(db_path), 'ai')

    try:
        os.makedirs(ai_db_dir, exist_ok=True)
    except PermissionError:
        # Shared path not writable, fall back to user's home directory.
        ai_db_dir = os.path.expanduser('~/.lsfMonitor/db/ai')
        os.makedirs(ai_db_dir, exist_ok=True)

    try:
        os.chmod(ai_db_dir, 0o1777)
    except PermissionError:
        pass

    db_file = os.path.join(ai_db_dir, 'ai_log.db')

    return db_file


def _ensure_user_table(db_file, user):
    """Create the per-user table if it does not exist (write mode, auto-creates db file)."""
    db_file_exists = os.path.exists(db_file)

    table_name = gen_table_name(user)
    key_string = common_sqlite3.gen_sql_table_key_string(TABLE_KEY_LIST, TABLE_KEY_TYPE_LIST)
    common_sqlite3.create_sql_table(db_file, '', table_name, key_string)

    if not db_file_exists and os.path.exists(db_file):
        try:
            os.chmod(db_file, 0o1777)
        except PermissionError:
            pass


def save_conversation(db_file, session_id, user, cluster, host, question, answer, tool_calls=None, resolution='unknown', keywords=''):
    """Insert a conversation record into the user's table."""
    _ensure_user_table(db_file, user)

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)

    if not keywords:
        keywords = extract_keywords(question + ' ' + answer)

    table_name = gen_table_name(user)
    value_list = [session_id, timestamp, user, cluster, host, question, answer, tool_calls_json, resolution, keywords]
    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
    common_sqlite3.insert_into_sql_table(db_file, '', table_name, value_string)


def update_resolution(db_file, session_id, resolution, user=''):
    """Update the resolution status of a conversation."""
    for table_name in _get_target_tables(db_file, user):
        set_condition = f"SET resolution='{resolution}'"
        where_condition = f"WHERE session_id='{session_id}'"
        common_sqlite3.update_sql_table_data(db_file, '', table_name, set_condition=set_condition, where_condition=where_condition)


def search_conversations(db_file, keyword='', user='', date_start='', date_end='', resolution='', limit=200):
    """
    Search conversations with multiple filters.
    If user is specified, search only that user's table; otherwise search all tables.
    Returns a merged dict {column: [values]}.
    """
    if not os.path.exists(db_file):
        return {}

    conditions = []

    if keyword:
        safe_keyword = keyword.replace("'", "''")
        conditions.append(f"(question LIKE '%{safe_keyword}%' OR answer LIKE '%{safe_keyword}%' OR keywords LIKE '%{safe_keyword}%')")

    if date_start:
        conditions.append(f"timestamp >= '{date_start} 00:00:00'")

    if date_end:
        conditions.append(f"timestamp <= '{date_end} 23:59:59'")

    if resolution and resolution != 'all':
        conditions.append(f"resolution='{resolution}'")

    where_clause = ''

    if conditions:
        where_clause = 'WHERE ' + ' AND '.join(conditions)

    merged = {}
    target_tables = _get_target_tables(db_file, user)

    for table_name in target_tables:
        select_condition = f"{where_clause} ORDER BY timestamp DESC LIMIT {limit}"
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', table_name, select_condition=select_condition)

        if data_dic:
            if not merged:
                merged = data_dic
            else:
                for key in data_dic:
                    merged.setdefault(key, []).extend(data_dic[key])

    # Sort merged results by timestamp descending and apply limit.
    if merged and 'timestamp' in merged:
        count = len(merged['timestamp'])

        if count > 1:
            indices = sorted(range(count), key=lambda i: merged['timestamp'][i] or '', reverse=True)

            for key in merged:
                merged[key] = [merged[key][i] for i in indices]

        # Apply limit.
        if count > limit:
            for key in merged:
                merged[key] = merged[key][:limit]

    return merged


def find_similar_conversations(db_file, user_message, limit=3):
    """
    Find similar solved conversations by keyword matching (across all users).
    Returns a list of dicts with keys: question, answer, tool_calls.
    """
    if not os.path.exists(db_file):
        return []

    query_keywords = extract_keywords(user_message).split()

    if not query_keywords:
        return []

    # Build LIKE conditions for keyword matching.
    like_parts = []

    for kw in query_keywords[:10]:
        safe_kw = kw.replace("'", "''")
        like_parts.append(f"(keywords LIKE '%{safe_kw}%' OR question LIKE '%{safe_kw}%')")

    if not like_parts:
        return []

    where_clause = f"WHERE resolution='solved' AND ({' OR '.join(like_parts)})"
    select_condition = f"{where_clause} ORDER BY timestamp DESC LIMIT {limit * 3}"

    # Search across all user tables.
    all_results = []

    for table_name in _get_target_tables(db_file, ''):
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', table_name, select_condition=select_condition)

        if not data_dic or 'question' not in data_dic:
            continue

        for i in range(len(data_dic['question'])):
            record_text = (data_dic.get('keywords', [''])[i] or '') + ' ' + (data_dic['question'][i] or '')
            record_text_lower = record_text.lower()
            score = sum(1 for kw in query_keywords if kw.lower() in record_text_lower)

            if score > 0:
                all_results.append({
                    'question': data_dic['question'][i],
                    'answer': data_dic['answer'][i],
                    'tool_calls': data_dic.get('tool_calls', ['[]'])[i],
                    'score': score,
                })

    all_results.sort(key=lambda x: x['score'], reverse=True)

    return all_results[:limit]


def get_all_conversations(db_file, user=''):
    """Get all conversation records, optionally filtered by user."""
    if not os.path.exists(db_file):
        return {}

    merged = {}

    for table_name in _get_target_tables(db_file, user):
        select_condition = 'ORDER BY timestamp DESC'
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', table_name, select_condition=select_condition)

        if data_dic:
            if not merged:
                merged = data_dic
            else:
                for key in data_dic:
                    merged.setdefault(key, []).extend(data_dic[key])

    # Sort merged results by timestamp descending.
    if merged and 'timestamp' in merged and len(merged['timestamp']) > 1:
        indices = sorted(range(len(merged['timestamp'])), key=lambda i: merged['timestamp'][i] or '', reverse=True)

        for key in merged:
            merged[key] = [merged[key][i] for i in indices]

    return merged


def cleanup_conversations(db_file, user, entries_limit):
    """
    Clean up a user's conversation table to keep at most entries_limit records.
    Deletes the oldest records (by timestamp) when the limit is exceeded.
    Returns the number of deleted rows.
    """
    if not os.path.exists(db_file):
        return 0

    table_name = gen_table_name(user)
    table_list = common_sqlite3.get_sql_table_list(db_file, '')

    if table_name not in table_list:
        return 0

    table_count = common_sqlite3.get_sql_table_count(db_file, '', table_name)

    if table_count == 'N/A' or int(table_count) <= entries_limit:
        return 0

    delete_count = int(table_count) - entries_limit
    common_sqlite3.delete_sql_table_rows(db_file, '', table_name, 'timestamp', 0, delete_count)

    return delete_count


def get_user_list(db_file):
    """Get all user names from the database by inspecting table names."""
    if not os.path.exists(db_file):
        return []

    table_list = common_sqlite3.get_sql_table_list(db_file, '')
    users = []
    prefix = 'conversations_'

    for table_name in table_list:
        if table_name.startswith(prefix):
            users.append(table_name[len(prefix):])

    return sorted(users)


def _get_target_tables(db_file, user):
    """
    Return the list of table names to operate on.
    If user is specified, return only that user's table (if it exists); otherwise all conversation tables.
    """
    table_list = common_sqlite3.get_sql_table_list(db_file, '')

    if user:
        table_name = gen_table_name(user)
        return [table_name] if table_name in table_list else []

    return [t for t in table_list if t.startswith('conversations_')]


def auto_judge_resolution(question, answer, tool_calls=None):
    """
    Automatically judge whether a conversation was solved/unsolved/unknown
    based on the content of the question, answer, and tool call results.
    """
    if not answer:
        return 'unknown'

    answer_lower = answer.lower()

    # Negative signals from AI answer — AI admits failure.
    ai_failure_patterns = [
        r'无法(确定|解决|找到|获取|帮助)',
        r'抱歉.{0,10}(无法|不能|没有办法)',
        r'sorry.{0,20}(cannot|unable|can\'t|don\'t know)',
        r'i\'m not (sure|able)',
        r'没有找到.{0,10}(相关|有效|可用)',
        r'暂时无法',
        r'超出.{0,5}(能力|范围)',
        r'建议.{0,5}(联系|咨询).{0,5}(管理员|support)',
    ]

    for pattern in ai_failure_patterns:
        if re.search(pattern, answer_lower):
            return 'unsolved'

    # Negative signals from tool calls — command execution errors.
    if tool_calls:
        calls = tool_calls if isinstance(tool_calls, list) else []

        if isinstance(tool_calls, str):
            try:
                calls = json.loads(tool_calls)
            except (json.JSONDecodeError, TypeError):
                calls = []

        if calls:
            error_count = 0
            total_count = len(calls)

            for call in calls:
                if not isinstance(call, dict):
                    continue

                result = (call.get('result', '') or '').lower()
                # Command returned error indicators.
                if re.search(r'(error|failed|permission denied|not found|no such|command not found|traceback|exception)', result):
                    error_count += 1

            # All tool calls failed → unsolved.
            if total_count > 0 and error_count == total_count:
                return 'unsolved'

    # Positive signals — AI provided a clear, substantive answer.
    # Check answer has reasonable length (not just a one-liner apology).
    # Use 20 chars as threshold since Chinese text is denser than English.
    if len(answer) > 20:
        # AI gave explanation with results/commands/data.
        positive_patterns = [
            r'(结果|输出|如下|以下|显示)',
            r'(可以看到|从.{0,5}可以)',
            r'(建议|解决方案|解决方法|步骤)',
            r'(已经|已成功|执行完成|完成)',
            r'(here|result|output|shows|following)',
            r'(you can|try|solution|resolved)',
        ]

        for pattern in positive_patterns:
            if re.search(pattern, answer_lower):
                return 'solved'

    # Tool calls executed and at least some succeeded.
    if tool_calls:
        calls = tool_calls if isinstance(tool_calls, list) else []

        if isinstance(tool_calls, str):
            try:
                calls = json.loads(tool_calls)
            except (json.JSONDecodeError, TypeError):
                calls = []

        if calls:
            success_count = 0

            for call in calls:
                if not isinstance(call, dict):
                    continue

                result = (call.get('result', '') or '')

                if len(result) > 20 and not re.search(r'(error|failed|permission denied|not found|command not found)', result.lower()):
                    success_count += 1

            if success_count > 0:
                return 'solved'

    return 'unknown'


def _ensure_insights_table(db_file):
    """Create the insights table if it does not exist."""
    key_string = common_sqlite3.gen_sql_table_key_string(INSIGHTS_KEY_LIST, INSIGHTS_KEY_TYPE_LIST)
    common_sqlite3.create_sql_table(db_file, '', INSIGHTS_TABLE, key_string)


def save_insight(db_file, session_id, insight, keywords, source_question):
    """Save a distilled insight from a solved conversation."""
    _ensure_insights_table(db_file)

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insight_id = uuid.uuid4().hex[:8]
    value_list = [insight_id, timestamp, session_id, insight, keywords, source_question]
    value_string = common_sqlite3.gen_sql_table_value_string(value_list)
    common_sqlite3.insert_into_sql_table(db_file, '', INSIGHTS_TABLE, value_string)


def find_relevant_insights(db_file, user_message, limit=5):
    """
    Find insights relevant to the user's question by keyword matching.
    Returns a list of insight strings.
    """
    if not os.path.exists(db_file):
        return []

    _ensure_insights_table(db_file)

    query_keywords = extract_keywords(user_message).split()

    if not query_keywords:
        return []

    like_parts = []

    for kw in query_keywords[:8]:
        safe_kw = kw.replace("'", "''")
        like_parts.append(f"keywords LIKE '%{safe_kw}%'")

    if not like_parts:
        return []

    where_clause = f"WHERE {' OR '.join(like_parts)}"
    select_condition = f"{where_clause} ORDER BY timestamp DESC LIMIT {limit * 3}"

    data_dic = common_sqlite3.get_sql_table_data(db_file, '', INSIGHTS_TABLE, select_condition=select_condition)

    if not data_dic or 'insight' not in data_dic:
        return []

    # Score and deduplicate.
    scored = []

    for i in range(len(data_dic['insight'])):
        kw_text = (data_dic.get('keywords', [''])[i] or '').lower()
        score = sum(1 for kw in query_keywords if kw.lower() in kw_text)

        if score > 0:
            scored.append((score, data_dic['insight'][i]))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate similar insights.
    seen = set()
    results = []

    for _, insight in scored:
        short = insight[:30]

        if short not in seen:
            seen.add(short)
            results.append(insight)

        if len(results) >= limit:
            break

    return results


def get_tool_preferences(db_file, user_message, min_count=2):
    """
    Analyze solved conversations matching user's question keywords.
    Returns a list of tool preference hints like "bjobs -p (used in 80% of similar cases)".
    """
    if not os.path.exists(db_file):
        return []

    query_keywords = extract_keywords(user_message).split()

    if not query_keywords:
        return []

    like_parts = []

    for kw in query_keywords[:8]:
        safe_kw = kw.replace("'", "''")
        like_parts.append(f"(keywords LIKE '%{safe_kw}%' OR question LIKE '%{safe_kw}%')")

    if not like_parts:
        return []

    where_clause = f"WHERE resolution='solved' AND tool_calls != '[]' AND ({' OR '.join(like_parts)})"
    select_condition = f"{where_clause} ORDER BY timestamp DESC LIMIT 50"

    # Scan all user tables.
    all_tool_calls = []

    for table_name in _get_target_tables(db_file, ''):
        data_dic = common_sqlite3.get_sql_table_data(db_file, '', table_name, select_condition=select_condition)

        if not data_dic or 'tool_calls' not in data_dic:
            continue

        for tc_json in data_dic['tool_calls']:
            if not tc_json or tc_json == '[]':
                continue

            try:
                calls = json.loads(tc_json) if isinstance(tc_json, str) else tc_json
            except (json.JSONDecodeError, TypeError):
                continue

            for call in calls:
                if isinstance(call, dict) and call.get('name'):
                    all_tool_calls.append(call)

    if not all_tool_calls:
        return []

    # Count tool+args patterns.
    pattern_count = {}

    for call in all_tool_calls:
        name = call.get('name', '')
        args = call.get('args', '')

        # Extract the core command from args (first word/command).
        if name == 'run_command' and args:
            cmd_match = re.match(r'(\S+(?:\s+-\S+)?)', args)
            pattern = cmd_match.group(1) if cmd_match else args.split()[0] if args.split() else name
        else:
            pattern = name

        pattern_count[pattern] = pattern_count.get(pattern, 0) + 1

    # Filter by minimum count and sort by frequency.
    preferences = []

    for pattern, count in sorted(pattern_count.items(), key=lambda x: x[1], reverse=True):
        if count >= min_count:
            preferences.append(f'{pattern} (used {count} times in similar solved cases)')

    return preferences[:5]


class InsightGeneratorThread(QThread):
    """Background thread that calls LLM to generate a one-line insight from a solved conversation."""

    finished_signal = pyqtSignal(str, str, str)  # session_id, insight, keywords

    def __init__(self, api_base_url, api_key, model_name, session_id, question, answer, tool_calls_json):
        super().__init__()
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.session_id = session_id
        self.question = question
        self.answer = answer
        self.tool_calls_json = tool_calls_json

    def run(self):
        try:
            insight = self._generate_insight()

            if insight:
                keywords = extract_keywords(self.question + ' ' + insight)
                self.finished_signal.emit(self.session_id, insight, keywords)
        except Exception:
            pass

    @staticmethod
    def _ensure_base_url(base_url):
        """Ensure OpenAI-compatible base_url ends with /v1."""
        if not any(f'/v{n}' in base_url for n in range(1, 10)):
            return base_url + '/v1'

        return base_url

    def _generate_insight(self):
        """Call LLM to distill one-line insight."""
        question = self.question[:200]
        answer = self.answer[:500]
        tool_info = ''

        if self.tool_calls_json and self.tool_calls_json != '[]':
            try:
                tools = json.loads(self.tool_calls_json) if isinstance(self.tool_calls_json, str) else self.tool_calls_json
                tool_names = [t.get('name', '') + '(' + (t.get('args', '')[:50]) + ')' for t in tools if isinstance(t, dict)]
                tool_info = f'\nTools used: {", ".join(tool_names)}'
            except (json.JSONDecodeError, TypeError):
                pass

        prompt = f"""Based on this solved HPC cluster support conversation, write ONE concise insight (under 80 chars) that would help resolve similar future questions. Focus on the key diagnosis step or solution pattern.

Question: {question}
Answer: {answer}{tool_info}

Write the insight in the same language as the question. Output ONLY the insight, nothing else."""

        from common import common_ai
        api_type = common_ai.detect_api_type(self.model_name)

        if api_type == 'anthropic':
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key, base_url=self.api_base_url)
            response = client.messages.create(
                model=self.model_name,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )

            return response.content[0].text.strip()
        else:
            import openai
            client = openai.OpenAI(api_key=self.api_key, base_url=self._ensure_base_url(self.api_base_url))
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
            )

            return response.choices[0].message.content.strip()


def extract_keywords(text):
    """
    Extract keywords from text (Chinese and English).
    Returns space-separated keywords string.
    """
    if not text:
        return ''

    # Split on non-alphanumeric and non-CJK characters.
    tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_]+', text.lower())

    keywords = []

    for token in tokens:
        # Skip short English words and stop words.
        if len(token) <= 2 and not re.match(r'[\u4e00-\u9fff]', token):
            continue

        if token in STOP_WORDS:
            continue

        if token not in keywords:
            keywords.append(token)

    return ' '.join(keywords[:30])


class AiReportThread(QThread):
    """
    Background thread that calls LLM to generate an HTML analysis report
    from conversation records.
    """
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, api_base_url, api_key, model_name, conversations_data, output_file):
        super().__init__()
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.conversations_data = conversations_data
        self.output_file = output_file

    _HEADING_ID_MAP = {'概览': 'sec-overview', '详细分析': 'sec-detail', '总结与建议': 'sec-summary'}

    def run(self):
        import time as _time

        try:
            _t_start = _time.time()
            analysis_content = self._generate_analysis()
            _t_llm_end = _time.time()

            analysis_content = self._inject_heading_ids(analysis_content)

            # Append timing info.
            timing_html = (
                f'<div class="header-info" style="margin-top:40px; padding-top:12px; border-top:1px solid #e0e0e0;">'
                f'<p>耗时统计 — LLM分析 {_t_llm_end - _t_start:.1f}s | '
                f'总耗时 {_t_llm_end - _t_start:.1f}s</p></div>'
            )
            analysis_content += '\n' + timing_html

            html = self._wrap_html(analysis_content)

            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(html)

            self.finished_signal.emit(self.output_file)
        except Exception as e:
            self.error_signal.emit(str(e))

    def _inject_heading_ids(self, content):
        """Inject id attributes into h2 headings that lack them, and strip any LLM-generated h1."""
        content = re.sub(r'<h1[^>]*>.*?</h1>\s*', '', content)

        for h_text, h_id in self._HEADING_ID_MAP.items():
            content = re.sub(
                r'<h2(?!\s[^>]*id=)([^>]*)>' + re.escape(h_text) + r'</h2>',
                rf'<h2 id="{h_id}"\1>{h_text}</h2>',
                content
            )

        return content

    def _build_statistics(self):
        """Build local statistics from conversation data."""
        data = self.conversations_data

        if not data or 'question' not in data:
            return '', []

        count = len(data['question'])

        # Resolution distribution.
        resolution_count = {}

        for i in range(count):
            res = data.get('resolution', ['unknown'] * count)[i] or 'unknown'
            resolution_count[res] = resolution_count.get(res, 0) + 1

        # Time distribution (by date).
        date_count = {}

        for i in range(count):
            ts = data.get('timestamp', [''] * count)[i] or ''
            date = ts[:10] if len(ts) >= 10 else 'unknown'
            date_count[date] = date_count.get(date, 0) + 1

        # Tool usage frequency.
        tool_count = {}

        for i in range(count):
            tc_json = data.get('tool_calls', ['[]'] * count)[i] or '[]'

            try:
                calls = json.loads(tc_json) if isinstance(tc_json, str) else tc_json
            except (json.JSONDecodeError, TypeError):
                continue

            for call in calls:
                if isinstance(call, dict) and call.get('name'):
                    name = call['name']
                    tool_count[name] = tool_count.get(name, 0) + 1

        # Build statistics text.
        stats_lines = []
        stats_lines.append(f'Total conversations: {count}')
        stats_lines.append(f'Resolution: {", ".join(f"{k}={v}" for k, v in sorted(resolution_count.items(), key=lambda x: x[1], reverse=True))}')

        if date_count:
            sorted_dates = sorted(date_count.keys())

            if sorted_dates:
                stats_lines.append(f'Date range: {sorted_dates[0]} ~ {sorted_dates[-1]}')

        if tool_count:
            top_tools = sorted(tool_count.items(), key=lambda x: x[1], reverse=True)[:10]
            stats_lines.append(f'Top tools: {", ".join(f"{k}({v})" for k, v in top_tools)}')

        # Build compact one-line summaries.
        compact_summaries = []

        for i in range(count):
            question = (data['question'][i] or '').replace('\n', ' ')[:100]
            resolution = data.get('resolution', ['unknown'] * count)[i] or 'unknown'
            compact_summaries.append(f'[{resolution}] {question}')

        return '\n'.join(stats_lines), compact_summaries

    def _call_llm(self, prompt):
        """Call LLM API and return the text response."""
        from common import common_ai
        api_type = common_ai.detect_api_type(self.model_name)

        if api_type == 'anthropic':
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key, base_url=self.api_base_url)
            response = client.messages.create(
                model=self.model_name,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        else:
            import openai
            client = openai.OpenAI(api_key=self.api_key, base_url=InsightGeneratorThread._ensure_base_url(self.api_base_url))
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            return response.choices[0].message.content

    def _generate_analysis(self):
        """Generate analysis with a single LLM call using local statistics + compact summaries."""
        statistics, compact_summaries = self._build_statistics()

        if not compact_summaries:
            return 'No conversation records found.'

        # Build the conversation list for LLM. Cap at ~800 lines to stay within context limits.
        max_lines = 800

        if len(compact_summaries) <= max_lines:
            conversations_text = '\n'.join(compact_summaries)
        else:
            conversations_text = '\n'.join(compact_summaries[:max_lines])
            conversations_text += f'\n... ({len(compact_summaries) - max_lines} more conversations omitted)'

        prompt = f"""请分析以下HPC集群AI助手的对话记录，生成一份分类分析报告。

## 统计摘要
{statistics}

## 对话列表（每行格式: [解决状态] 问题摘要）
{conversations_text}

请严格按以下格式输出（直接输出HTML内容，不要markdown代码块，不要输出任何h1标题）。
注意：三个h2章节必须全部输出，每个分类的每个字段都必须有实际内容（不能为空），不要出现"其他"这种模糊分类。

<h2 id="sec-overview">概览</h2>
<table>
<tr><th>问题分类</th><th class="r">数量</th><th class="r">占比</th><th class="r">解决率</th></tr>
<!-- 每行: <tr><td>分类名</td><td class="r">数量</td><td class="r">百分比</td><td class="r">解决率</td></tr> -->
</table>

<h2 id="sec-detail">详细分析</h2>
<!-- 每个分类用一张 issue 卡片，格式如下: -->
<div class="issue mid">
<p><span class="badge mid">分类名</span><b>分类标题（含数量）</b></p>
<p><b>常见原因：</b>具体原因描述</p>
<p><b>典型案例：</b>具体案例描述</p>
<p><b>推荐解决方案：</b>具体解决方案</p>
</div>
<!-- badge 颜色规则: 占比>=30% 用 high, 占比>=15% 用 mid, 其余用 low -->
<!-- issue 的 class 对应: high/mid/low -->

<h2 id="sec-summary">总结与建议</h2>
<div class="card-panel assess"><b>总体评估：</b>一句话总结问题趋势</div>
<div class="card-panel">
<b>改进建议：</b>
<ul>
<li>具体可执行的建议1</li>
<li>具体可执行的建议2</li>
</ul>
</div>
"""
        return self._call_llm(prompt)

    def _wrap_html(self, content):
        """Wrap analysis content into a full HTML page with side navigation."""
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        total = len(self.conversations_data.get('question', []))

        # Build nav links dynamically from actual h2 headings in content.
        nav_links = ''

        for match in re.finditer(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', content):
            h_id, h_text = match.group(1), match.group(2)
            nav_links += f'<a href="#{h_id}">{h_text}</a>\n'

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AI Problem Analysis Report</title>
<style>
:root {{ --blue:#3498db; --blue-d:#2980b9; --ink:#2c3e50; --muted:#7f8c8d;
        --red:#e74c3c; --orange:#e67e22; --green:#27ae60; --line:#e3e8ee;
        --bg:#eef2f6; --surface:#ffffff; --surface-2:#f6f9fc; --surface-3:#fafcfe;
        --shadow:rgba(15,30,50,.06); --code-bg:#eef2f7; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --blue:#4aa3df; --blue-d:#5dade2; --ink:#e6edf3; --muted:#94a3b3;
          --red:#ef6e63; --orange:#ec9b4d; --green:#3fc77a; --line:#283341;
          --bg:#0e131a; --surface:#161d27; --surface-2:#1b232f; --surface-3:#1b232f;
          --shadow:rgba(0,0,0,.45); --code-bg:#202a37; }}
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
     padding-left: 11px; border-left: 3px solid var(--green); opacity: .92; }}
p {{ margin: 10px 0; font-size: 14.5px; }}
b {{ color: var(--ink); }}
ul, ol {{ padding-left: 22px; }}
li {{ margin: 6px 0; }}

.header-info {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}

/* 表格 */
table {{ border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px;
        background: var(--surface); border-radius: 10px; overflow: hidden;
        box-shadow: 0 1px 3px var(--shadow); }}
th {{ background: linear-gradient(135deg, var(--blue), var(--blue-d)); color: #fff;
     padding: 10px 12px; text-align: left; font-weight: 600; letter-spacing: .02em; }}
td {{ padding: 9px 12px; border-bottom: 1px solid var(--line); }}
tr:last-child td {{ border-bottom: none; }}
tr:nth-child(even) td {{ background: var(--surface-2); }}
td.r, th.r {{ text-align: right; font-variant-numeric: tabular-nums; }}

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

/* 卡片面板 */
.card-panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
              padding: 20px 24px; margin-bottom: 18px; box-shadow: 0 1px 3px var(--shadow); }}
.card-panel.assess {{ background: color-mix(in srgb, var(--blue) 7%, var(--surface));
                     border-left: 4px solid var(--blue); }}

code {{ background: var(--code-bg); padding: 2px 5px; border-radius: 4px; font-size: 13px;
       font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }}
pre {{ background: #1f2b38; color: #ecf0f1; padding: 12px 14px; border-radius: 8px;
      overflow-x: auto; font-size: 13px; line-height: 1.5; }}
pre code {{ background: none; color: inherit; padding: 0; }}

/* 左侧导航栏 */
.side-nav {{ position: fixed; top: 0; left: 0; width: 220px; height: 100vh; overflow-y: auto;
            background: var(--surface); border-right: 1px solid var(--line); padding: 24px 0;
            box-shadow: 2px 0 8px var(--shadow); z-index: 100; }}
.side-nav .nav-title {{ font-size: 14px; font-weight: 700; color: var(--ink); padding: 0 18px 14px;
                       border-bottom: 1px solid var(--line); margin-bottom: 10px; }}
.side-nav a {{ display: block; padding: 9px 18px; font-size: 13px; color: var(--muted);
              text-decoration: none; border-left: 3px solid transparent; transition: all .15s; }}
.side-nav a:hover {{ color: var(--ink); background: var(--surface-2); }}
.side-nav a.active {{ color: var(--blue-d); border-left-color: var(--blue); font-weight: 600;
                     background: color-mix(in srgb, var(--blue) 6%, var(--surface)); }}
@media (max-width: 1359px) {{ .side-nav {{ display: none; }} }}
</style>
</head>
<body>
<nav class="side-nav">
<div class="nav-title">目录导航</div>
{nav_links}</nav>
<div class="wrap">
<h1>用户常见LSF问题分析及解决方案</h1>
<div class="header-info">
<p>Generated: {timestamp} | Total conversations: {total}</p>
</div>
{content}
</div>
<script>
(function () {{
  var nav = document.querySelector('.side-nav');
  if (!nav) return;
  var links = nav.querySelectorAll('a[href^="#"]');
  var sections = [];
  links.forEach(function (a) {{
    var id = a.getAttribute('href').slice(1);
    var el = document.getElementById(id);
    if (el) sections.push({{ el: el, link: a }});
  }});
  if (!sections.length) return;
  function onScroll() {{
    var scrollY = window.scrollY || window.pageYOffset;
    var active = sections[0];
    for (var i = 0; i < sections.length; i++) {{
      if (sections[i].el.offsetTop - 80 <= scrollY) active = sections[i];
    }}
    links.forEach(function (a) {{ a.classList.remove('active'); }});
    if (active) active.link.classList.add('active');
  }}
  window.addEventListener('scroll', onScroll);
  onScroll();
  links.forEach(function (a) {{
    a.addEventListener('click', function (e) {{
      var id = a.getAttribute('href').slice(1);
      var target = document.getElementById(id);
      if (target) {{ e.preventDefault(); target.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }}
    }});
  }});
}})();
</script>
</body>
</html>"""
