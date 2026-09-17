import os
import re
import sys
import socket
import getpass
import datetime
import subprocess
import ast as _ast
import operator as _operator


def bprint(message, color='', background_color='', display_method='', date_format='', level='', indent=0, end='\n', save_file='', save_file_method='a'):
    """
    Enhancement of "print" function.

    color:            Specify font foreground color, default to follow the terminal settings.
    background_color: Specify font background color, default to follow the terminal settings.
    display_method:   Specify font display method, default to follow the terminal settings.
    date_format:      Will show date/time information before the message, such as "%Y_%m_%d %H:%M:%S". Default is "", means silent mode.
    level:            Will show message level information after date/time information, default is "", means show nothing.
    indent:           How much spaces to indent for specified message (with level information), default is 0, means no indentation.
    end:              Specify the character at the end of the output, default is "\n".
    save_file:        Save message into specified file, default is "", means save nothing.
    save_file_method: Save message with "append" or "write" mode, default is "append" mode.

    For "color" and "background_color":
    -----------------------------------------------
    字体色   |   背景色   |   Color    |   颜色描述
    -----------------------------------------------
    30       |   40       |   black    |   黑色
    31       |   41       |   red      |   红色
    32       |   42       |   green    |   绿色
    33       |   43       |   yellow   |   黃色
    34       |   44       |   blue     |   蓝色
    35       |   45       |   purple   |   紫色
    36       |   46       |   cyan     |   青色
    37       |   47       |   white    |   白色
    -----------------------------------------------

    For "display_method":
    ---------------------------
    显示方式   |   效果
    ---------------------------
    0          |   终端默认设置
    1          |   高亮显示
    4          |   使用下划线
    5          |   闪烁
    7          |   反白显示
    8          |   不可见
    ---------------------------

    For "level":
    -------------------------------------------------------------
    层级      |   说明
    -------------------------------------------------------------
    Debug     |   程序运行的详细信息, 主要用于调试.
    Info      |   程序运行过程信息, 主要用于将系统状态反馈给用户.
    Warning   |   表明会出现潜在错误, 但是一般不影响系统继续运行.
    Error     |   发生错误, 不确定系统是否可以继续运行.
    Fatal     |   发生严重错误, 程序会停止运行并退出.
    -------------------------------------------------------------

    For "save_file_method":
    -----------------------------------------------------------
    模式   |   说明
    -----------------------------------------------------------
    a      |   append mode, append content to existing file.
    w      |   write mode, create a new file and write content.
    -----------------------------------------------------------
    """
    # Check arguments.
    color_dic = {'black': 30,
                 'red': 31,
                 'green': 32,
                 'yellow': 33,
                 'blue': 34,
                 'purple': 35,
                 'cyan': 36,
                 'white': 37}

    if color:
        if (color not in color_dic.keys()) and (color not in color_dic.values()):
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{color}": Invalid color setting, it must follow below rules.', date_format='', color=33, display_method=1)
            bprint('''
                    ----------------------------------
                    字体色   |   Color    |   颜色描述
                    ----------------------------------
                    30       |   black    |   黑色
                    31       |   red      |   红色
                    32       |   green    |   绿色
                    33       |   yellow   |   黃色
                    34       |   blue     |   蓝色
                    35       |   purple   |   紫色
                    36       |   cyan     |   青色
                    37       |   white    |   白色
                    ----------------------------------
            ''', date_format='', color=33, display_method=1)

            return

    background_color_dic = {'black': 40,
                            'red': 41,
                            'green': 42,
                            'yellow': 43,
                            'blue': 44,
                            'purple': 45,
                            'cyan': 46,
                            'white': 47}

    if background_color:
        if (background_color not in background_color_dic.keys()) and (background_color not in background_color_dic.values()):
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{background_color}": Invalid background_color setting, it must follow below rules.', date_format='', color=33, display_method=1)
            bprint('''
                    ----------------------------------
                    背景色   |   Color    |   颜色描述
                    ----------------------------------
                    40       |   black    |   黑色
                    41       |   red      |   红色
                    42       |   green    |   绿色
                    43       |   yellow   |   黃色
                    44       |   blue     |   蓝色
                    45       |   purple   |   紫色
                    46       |   cyan     |   青色
                    47       |   white    |   白色
                    ----------------------------------
            ''', date_format='', color=33, display_method=1)

            return

    if display_method:
        valid_display_method_list = [0, 1, 4, 5, 7, 8]

        if display_method not in valid_display_method_list:
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{display_method}": Invalid display_method setting, it must be integer between 0,1,4,5,7,8.', date_format='', color=33, display_method=1)
            bprint('''
                    ----------------------------
                    显示方式   |    效果
                    ----------------------------
                    0          |    终端默认设置
                    1          |    高亮显示
                    4          |    使用下划线
                    5          |    闪烁
                    7          |    反白显示
                    8          |    不可见
                    ----------------------------
            ''', date_format='', color=33, display_method=1)

            return

    if level:
        valid_level_list = ['Debug', 'Info', 'Warning', 'Error', 'Fatal']

        if level not in valid_level_list:
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{level}": Invalid level setting, it must be Debug/Info/Warning/Error/Fatal.', date_format='', color=33, display_method=1)
            bprint('''
                    -------------------------------------------------------------
                    层级      |   说明
                    -------------------------------------------------------------
                    Debug     |   程序运行的详细信息, 主要用于调试.
                    Info      |   程序运行过程信息, 主要用于将系统状态反馈给用户.
                    Warning   |   表明会出现潜在错误, 但是一般不影响系统继续运行.
                    Error     |   发生错误, 不确定系统是否可以继续运行.
                    Fatal     |   发生严重错误, 程序会停止运行并退出.
                    -------------------------------------------------------------
            ''', date_format='', color=33, display_method=1)
            return

    if not re.match(r'^\d+$', str(indent)):
        bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
        bprint(f'                    {message}', date_format='', color=33, display_method=1)
        bprint(f'*Warning* (bprint): "{indent}": Invalid indent setting, it must be a positive integer, will reset to "0".', date_format='', color=33, display_method=1)

        indent = 0

    if save_file:
        valid_save_file_method_list = ['a', 'append', 'w', 'write']

        # Normalize 'append'/'write' to the open() mode letters 'a'/'w' so the
        # accepted aliases actually work (open() only accepts 'a'/'w' and would
        # otherwise raise ValueError, caught as a warning and the file skipped).
        if save_file_method == 'append':
            save_file_method = 'a'
        elif save_file_method == 'write':
            save_file_method = 'w'

        if save_file_method not in valid_save_file_method_list:
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{save_file_method}": Invalid save_file_method setting, it must be "a" or "w".', date_format='', color=33, display_method=1)
            bprint('''
                    -----------------------------------------------------------
                    模式   |   说明
                    -----------------------------------------------------------
                    a      |   append mode, append content to existing file.
                    w      |   write mode, create a new file and write content.
                    -----------------------------------------------------------
            ''', date_format='', color=33, display_method=1)

            return

    # Set default color/background_color/display_method setting for different levels.
    if level:
        if level == 'Warning':
            if not display_method:
                display_method = 1

            if not color:
                color = 33
        elif level == 'Error':
            if not display_method:
                display_method = 1

            if not color:
                color = 31
        elif level == 'Fatal':
            if not display_method:
                display_method = 1

            if not background_color:
                background_color = 41

            if background_color == 41:
                if not color:
                    color = 37
            else:
                if not color:
                    color = 35

    # Get final color setting.
    final_color_setting = ''

    if color or background_color or display_method:
        final_color_setting = '\033['

        if display_method:
            final_color_setting = str(final_color_setting) + str(display_method)

        if color:
            if not re.match(r'^\d{2}$', str(color)):
                color = color_dic[color]

            if re.match(r'^.*\d$', final_color_setting):
                final_color_setting = str(final_color_setting) + ';' + str(color)
            else:
                final_color_setting = str(final_color_setting) + str(color)

        if background_color:
            if not re.match(r'^\d{2}$', str(background_color)):
                background_color = background_color_dic[background_color]

            if re.match(r'^.*\d$', final_color_setting):
                final_color_setting = str(final_color_setting) + ';' + str(background_color)
            else:
                final_color_setting = str(final_color_setting) + str(background_color)

        final_color_setting = str(final_color_setting) + 'm'

    # Get current_time if date_format is specified.
    current_time = ''

    if date_format:
        try:
            current_time = datetime.datetime.now().strftime(date_format)
        except Exception:
            bprint('*Warning* (bprint): Meet some setting problem with below message.', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): "{date_format}": Invalid date_format setting, suggest to use the default setting.', date_format='', color=33, display_method=1)
            return

    # Print message with specified format.
    final_message = ''

    if current_time:
        final_message = str(final_message) + '[' + str(current_time) + '] '

    if indent > 0:
        final_message = str(final_message) + ' ' * indent

    if level:
        final_message = str(final_message) + '*' + str(level) + '*: '

    final_message = str(final_message) + str(message)

    if final_color_setting:
        final_message_with_color = final_color_setting + str(final_message) + '\033[0m'
    else:
        final_message_with_color = final_message

    print(final_message_with_color, end=end)

    # Save file.
    if save_file:
        try:
            with open(save_file, save_file_method) as SF:
                SF.write(str(final_message) + '\n')
        except Exception as warning:
            bprint(f'*Warning* (bprint): Meet some problem when saving below message into file "{save_file}".', date_format='', color=33, display_method=1)
            bprint(f'                    {message}', date_format='', color=33, display_method=1)
            bprint(f'*Warning* (bprint): {warning}', date_format='', color=33, display_method=1)
            return


def run_command(command, mystdin=subprocess.DEVNULL, mystdout=subprocess.PIPE, mystderr=subprocess.PIPE, timeout=None):
    """
    Run system command with subprocess.Popen, get returncode/stdout/stderr.

    stdin defaults to DEVNULL so interactive prompts (e.g. ssh host-key
    confirmation) get EOF immediately and exit instead of blocking forever.
    """
    SP = subprocess.Popen(command, shell=True, stdin=mystdin, stdout=mystdout, stderr=mystderr)

    try:
        (stdout, stderr) = SP.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        SP.kill()
        SP.communicate()
        return 1, b'', f'Command timed out after {timeout}s: {command}'.encode()

    return SP.returncode, stdout, stderr


def get_job_range_dic(job_list, range_size=100000):
    """
    Get job range string "***_***" based the jobid.
    """
    job_range_dic = {}

    for job in job_list:
        job_org = job
        job = re.sub(r'\[.*', '', job)
        job_head = (int(int(job) / range_size)) * range_size
        job_tail = job_head + range_size - 1
        job_range = str(job_head) + '_' + str(job_tail)
        job_range_dic.setdefault(job_range, [])
        job_range_dic[job_range].append(job_org)

    return job_range_dic


def resolve_job_data_db(job_data_dir, jobid):
    """Resolve the job_data db file for a jobid, trying new 100K-shard first
    then legacy 1M-shard.

    range_size for job_data changed from 1M to 100K (smaller files = smaller
    blast radius on malformed). Old sampled data still lives in 1M-shard files
    until the 30-day cleanup rolls them out, so read paths must try both.
    Returns the db file path if found, else ''.
    """
    job_num = re.sub(r'\[.*', '', str(jobid))

    try:
        job_num_int = int(job_num)
    except ValueError:
        return ''

    for range_size in (100000, 1000000):
        head = (job_num_int // range_size) * range_size
        db_file = os.path.join(str(job_data_dir), f'{head}_{head + range_size - 1}.db')

        if os.path.exists(db_file):
            return db_file

    return ''


def write_csv(csv_file, content_dic):
    """
    Write csv with content_dic.
    content_dic = {
        'title_1': [column1_1, columne1_2, ...],
        'title_2': [column2_1, columne2_2, ...],
        ...
    }
    """
    import pandas
    pandas.DataFrame(content_dic).to_csv(csv_file, index=False)


def create_dir(dir_path, permission=0o1777):
    """
    Create dir with specified permission.

    Golden permission spec: docs/db_permissions.md (single source of truth).
    Also chmod-s existing dirs (repairs 0o755 left by a cp -r / umask),
    so a re-run self-heals historical permission drift. PermissionError
    (non-owner chmod) is silently skipped; other errors still sys.exit(1).
    """
    try:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        os.chmod(dir_path, permission)
    except PermissionError:
        # Non-owner: cannot chmod an existing dir we don't own. Leave it
        # as-is — callers that need write access will hit their fallback.
        pass
    except Exception as error:
        bprint(f'Failed on creating directory "{dir_path}".', level='Error')
        bprint(error, color='red', display_method=1, indent=9)
        sys.exit(1)


# paramiko 为可选依赖,仅 ssh_client 使用;未安装时降级为不可用。
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    paramiko = None
    HAS_PARAMIKO = False


def ssh_client(host_name='', port=22, user_name=getpass.getuser(), password='', command='', reconnect=False, timeout=10):
    """
    Ssh specified host, execute specified command, get stdout information (return stdout_list).
    """
    if not HAS_PARAMIKO:
        bprint('paramiko is not installed, ssh_client is unavailable.', level='Warning')

        return []

    stdout_list = []
    client = paramiko.SSHClient()

    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host_name, port, user_name, password=password, timeout=timeout)

        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        result = stdout.read().decode()
        stdout_list = str(result).splitlines()

        if exit_status != 0:
            bprint('Ssh command exited with non-zero status: ' + str(exit_status), level='Error')
    except paramiko.AuthenticationException:
        bprint('Authentication failed.', level='Error')
    except paramiko.SSHException as ssh_ex:
        bprint('Ssh connection error: ' + str(ssh_ex), level='Error')
    except socket.error as socket_error:
        if not reconnect and sys.stdin.isatty():
            bprint('Ssh fail.', level='Warning')

            password = getpass.getpass('            Please input password:')
            stdout_list = ssh_client(host_name=host_name, port=port, user_name=user_name, password=password, command=command, reconnect=True)
        else:
            bprint('Socket error: ' + str(socket_error), level='Error')
    except Exception as error:
        bprint('Meet below error when ssh ' + str(host_name), level='Error')
        bprint(error, color='red', display_method=1, indent=9)
    finally:
        client.close()

    return stdout_list


# ==========================================================================
# Safe expression evaluator (for the Run panel result filtering). A restricted
# AST evaluator: allows boolean/comparison/binary ops, indexing/slicing, and a
# whitelist of functions. Disallows attribute access, imports, comprehensions,
# and underscore names (blocks __builtins__ escapes).
# ==========================================================================
_SAFE_BINOPS = {
    _ast.Add: _operator.add, _ast.Sub: _operator.sub, _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv, _ast.FloorDiv: _operator.floordiv, _ast.Mod: _operator.mod,
    _ast.Pow: _operator.pow,
}
_SAFE_CMPOPS = {
    _ast.Eq: _operator.eq, _ast.NotEq: _operator.ne, _ast.Lt: _operator.lt,
    _ast.LtE: _operator.le, _ast.Gt: _operator.gt, _ast.GtE: _operator.ge,
    _ast.In: lambda a, b: a in b, _ast.NotIn: lambda a, b: a not in b,
}
_SAFE_UNARY = {_ast.UAdd: _operator.pos, _ast.USub: _operator.neg, _ast.Not: _operator.not_}

# Whitelisted built-in functions (callable only by direct name, no attribute access).
_SAFE_FUNCS = {
    'len': len, 'int': int, 'float': float, 'str': str, 'bool': bool,
    'abs': abs, 'min': min, 'max': max, 'round': round,
    'upper': lambda x: x.upper() if isinstance(x, str) else str(x).upper(),
    'lower': lambda x: x.lower() if isinstance(x, str) else str(x).lower(),
    'startswith': lambda s, p: str(s).startswith(p),
    'endswith': lambda s, p: str(s).endswith(p),
    'contains': lambda s, p: str(p) in str(s),
}


class SafeEvalError(Exception):
    pass


class _SafeEvalVisitor(_ast.NodeVisitor):
    """
    Evaluate a restricted Python expression against the provided names dict.
    """
    def __init__(self, names):
        self.names = names

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        return node.value

    def visit_Name(self, node):
        if not isinstance(node.ctx, _ast.Load):
            raise SafeEvalError('disallowed name context')

        name = node.id

        if name.startswith('_'):
            raise SafeEvalError(f'access to "{name}" is not allowed')

        if name not in self.names:
            raise SafeEvalError(f'unknown name "{name}"')

        return self.names[name]

    def visit_BoolOp(self, node):
        values = [self.visit(v) for v in node.values]

        if isinstance(node.op, _ast.And):
            result = True

            for v in values:
                result = result and v

            return result
        else:
            result = False

            for v in values:
                result = result or v

            return result

    def visit_UnaryOp(self, node):
        op = _SAFE_UNARY.get(type(node.op))

        if op is None:
            raise SafeEvalError('disallowed unary op')

        return op(self.visit(node.operand))

    def visit_BinOp(self, node):
        op = _SAFE_BINOPS.get(type(node.op))

        if op is None:
            raise SafeEvalError('disallowed binary op')

        return op(self.visit(node.left), self.visit(node.right))

    def visit_Compare(self, node):
        left = self.visit(node.left)
        result = True

        for op, comparator in zip(node.ops, node.comparators):
            cmp = _SAFE_CMPOPS.get(type(op))

            if cmp is None:
                raise SafeEvalError('disallowed comparison op')

            right = self.visit(comparator)
            result = result and cmp(left, right)
            left = right

        return result

    def visit_List(self, node):
        return [self.visit(e) for e in node.elts]

    def visit_Tuple(self, node):
        return tuple(self.visit(e) for e in node.elts)

    def visit_Set(self, node):
        return set(self.visit(e) for e in node.elts)

    def visit_Call(self, node):
        # Only direct calls to whitelisted function names: func(...).
        if not isinstance(node.func, _ast.Name):
            raise SafeEvalError('only direct function calls are allowed')

        func_name = node.func.id

        if func_name not in _SAFE_FUNCS:
            raise SafeEvalError(f'function "{func_name}" is not allowed')

        if node.keywords:
            raise SafeEvalError('keyword arguments are not allowed')

        args = [self.visit(a) for a in node.args]

        try:
            return _SAFE_FUNCS[func_name](*args)
        except Exception as e:
            raise SafeEvalError(f'function "{func_name}" failed: {e}') from e

    def visit_Subscript(self, node):
        value = self.visit(node.value)

        if isinstance(node.slice, _ast.Slice):
            lower = self.visit(node.slice.lower) if node.slice.lower is not None else None
            upper = self.visit(node.slice.upper) if node.slice.upper is not None else None
            step = self.visit(node.slice.step) if node.slice.step is not None else None
            return value[lower:upper:step]

        index = self.visit(node.slice)

        return value[index]

    def generic_visit(self, node):
        raise SafeEvalError(f'disallowed expression element: {type(node).__name__}')


def safe_eval_expr(expression, names):
    """
    Safely evaluate a boolean/arithmetic expression against the ``names`` dict.

    Returns the evaluated value. Raises SafeEvalError on any disallowed construct.
    Example: safe_eval_expr("'group1' in groups", {'groups': ['group1', 'group2']}) -> True
    """
    if not isinstance(expression, str) or not expression.strip():
        raise SafeEvalError('empty expression')

    tree = _ast.parse(expression, mode='eval')

    return _SafeEvalVisitor(names).visit(tree)


def match_exact_or_fuzzy(keywords, items):
    """
    Match keywords against items with exact-preferred semantics.

    For each keyword: if it exactly equals any item (case-sensitive), only that
    exact item is kept for the keyword; otherwise fall back to fuzzy (substring,
    case-insensitive) match. The result is the union across all keywords.

    So "VCS-BASE-RUNTIME" hits the exact feature and drops the fuzzy
    "VCS-Base-Runtime-Pkg"; a keyword with no exact match still uses fuzzy.
    """
    matched_items = []

    for keyword in keywords:
        if keyword in items:
            if keyword not in matched_items:
                matched_items.append(keyword)
        else:
            for item in items:
                if re.search(re.escape(keyword.lower()), item.lower()) and item not in matched_items:
                    matched_items.append(item)

    return matched_items
