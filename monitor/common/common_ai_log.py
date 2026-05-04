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
    table_name = gen_table_name(user)
    key_string = common_sqlite3.gen_sql_table_key_string(TABLE_KEY_LIST, TABLE_KEY_TYPE_LIST)
    common_sqlite3.create_sql_table(db_file, '', table_name, key_string)


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
    If user is specified, return only that user's table; otherwise all conversation tables.
    """
    if user:
        return [gen_table_name(user)]

    table_list = common_sqlite3.get_sql_table_list(db_file, '')

    return [t for t in table_list if t.startswith('conversations_')]


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

    def run(self):
        try:
            analysis_content = self._generate_analysis()
            html = self._wrap_html(analysis_content)

            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(html)

            self.finished_signal.emit(self.output_file)
        except Exception as e:
            self.error_signal.emit(str(e))

    def _build_summaries(self):
        """Build conversation summaries, batching if too many."""
        data = self.conversations_data

        if not data or 'question' not in data:
            return []

        summaries = []
        count = len(data['question'])

        for i in range(count):
            question = data['question'][i] or ''
            answer = data['answer'][i] or ''
            resolution = data.get('resolution', ['unknown'] * count)[i] or 'unknown'
            tool_calls = data.get('tool_calls', ['[]'] * count)[i] or '[]'

            # Truncate for LLM context.
            summary = f"Q: {question[:300]}\nA: {answer[:500]}\nResolution: {resolution}\nTools: {tool_calls[:200]}"
            summaries.append(summary)

        return summaries

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
            client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base_url)
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            return response.choices[0].message.content

    def _generate_analysis(self):
        """Generate analysis by calling LLM (batch if needed)."""
        summaries = self._build_summaries()

        if not summaries:
            return 'No conversation records found.'

        batch_size = 50
        all_analysis = []

        for start in range(0, len(summaries), batch_size):
            batch = summaries[start:start + batch_size]
            batch_text = '\n---\n'.join(batch)

            prompt = f"""请分析以下 {len(batch)} 条HPC集群AI助手的对话记录，生成一份分类分析报告。

对话记录:
{batch_text}

请按以下格式输出（直接输出HTML内容，不要markdown代码块）:

<h2>概览</h2>
<table border="1" cellpadding="5" cellspacing="0">
<tr><th>问题分类</th><th>数量</th><th>占比</th></tr>
<!-- 列出所有问题分类及统计 -->
</table>

<h2>详细分析</h2>
<!-- 每个分类: -->
<h3>分类名称</h3>
<p><b>常见原因:</b> ...</p>
<p><b>典型案例:</b> ...</p>
<p><b>推荐解决方案:</b> ...</p>

<h2>总结与建议</h2>
<p>...</p>
"""
            result = self._call_llm(prompt)
            all_analysis.append(result)

        if len(all_analysis) == 1:
            return all_analysis[0]

        # Merge multiple batch results.
        merge_prompt = f"""请将以下 {len(all_analysis)} 份分析报告合并为一份完整报告，保持相同的HTML格式。去重合并分类，重新统计数量和占比。

{"---REPORT---".join(all_analysis)}
"""
        return self._call_llm(merge_prompt)

    def _wrap_html(self, content):
        """Wrap analysis content into a full HTML page."""
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        total = len(self.conversations_data.get('question', []))

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AI Problem Analysis Report</title>
<style>
body {{ font-family: "Microsoft YaHei", Arial, sans-serif; margin: 40px; background: #f9f9f9; }}
h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
h2 {{ color: #2980b9; margin-top: 30px; }}
h3 {{ color: #27ae60; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
th {{ background-color: #3498db; color: white; padding: 10px; }}
td {{ padding: 8px; border: 1px solid #ddd; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
p {{ line-height: 1.8; }}
.header-info {{ color: #7f8c8d; font-size: 14px; margin-bottom: 20px; }}
</style>
</head>
<body>
<h1>用户常见LSF问题分析及解决方案</h1>
<div class="header-info">
<p>Generated: {timestamp} | Total conversations: {total}</p>
</div>
{content}
</body>
</html>"""
