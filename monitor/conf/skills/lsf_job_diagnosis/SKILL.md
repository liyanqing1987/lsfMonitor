---
name: lsf_job_diagnosis
description: 专业诊断LSF作业全场景异常，覆盖PEND挂起、EXIT失败、RUN卡住无响应等状态，精准定位根因，输出可直接执行的修复方案与预防建议
version: 1.1.0
tags:
  - pending
  - pend
  - 排队
  - 退出码
  - exit code
  - term_
  - stuck
  - 卡住
  - diagnose
  - troubleshoot
  - 作业排查
  - 诊断
---

# LSF 作业专业诊断技能

## 功能说明

全流程诊断 IBM Spectrum LSF 作业异常，覆盖以下场景：

- **PEND**：作业一直排队不启动
- **EXIT**：作业异常退出/运行失败
- **RUN 卡住**：作业运行中但无输出、无响应
- **资源超限**：内存、CPU、运行时长、进程数、Swap、磁盘限制触发
- **调度问题**：队列限制、用户权限、主机匹配、依赖条件、License 不足
- **系统问题**：信号终止、目录权限、NFS 挂载、环境变量不兼容

## 触发条件

用户提问涉及以下内容时自动启用：

- 作业为什么 PEND / 一直不跑 / 排队不动
- 作业 EXIT / 失败了，怎么查原因
- 退出码 137/139/143/255 是什么意思
- 作业被杀了 / 终止了，什么原因
- 作业 RUN 状态但卡住了 / 没输出
- 怎么看已完成作业的日志 / 历史记录

---

## 诊断全流程

### Step 0：前置环境检查（必做）

1. 执行 `lsid` 验证集群连接，确认返回集群名称和管理节点信息
2. 确认当前用户有权限查询目标作业

### Step 1：采集作业核心信息

**情况 A：作业仍在集群中（PEND/RUN/刚 EXIT，bjobs 可查）**

```bash
bjobs -l <job_id>       # 全量作业信息（状态、资源、终止原因、Pending 原因）
bjobs -p -3 <job_id>    # 精简 Pending 原因（TOP3，PEND 状态必做）
bpeek <job_id>          # 实时查看作业输出（RUN 状态必做）
```

**情况 B：作业已完成（DONE/EXIT 超 1 小时，bjobs 无记录）**

LSF 默认仅保留已完成作业 1 小时的 bjobs 记录，需用历史查询：

```bash
bhist -l <job_id>       # 已完成作业的全量历史详情
bhist -n 10 <job_id>    # 调度与执行历史记录
```

**必须提取的核心字段：**

- 基础信息：Job ID、作业状态、提交用户、队列名称、提交节点、执行节点
- 调度信息：资源请求（-R/-M/-n 等参数）、Pending 原因、依赖条件
- 异常信息：Exit Code、Termination Reason、资源使用峰值、限制阈值
- 运行信息：工作目录（CWD）、执行命令、stdout/stderr 重定向路径

### Step 2：按作业状态分支诊断

#### 状态 1：PEND（作业排队未启动）

> 核心定位：为什么调度器没有分配资源启动作业

**必做检查：**

```bash
bjobs -p -3 <job_id>            # TOP3 Pending 原因
bqueues -l <queue_name>         # 队列状态、可用槽位、资源限制
bhosts -w                       # 集群节点状态（OK/Closed/Unavail/Busy）
lshosts -l <exec_host>          # 节点资源与作业请求是否匹配
```

**高频根因与解决方案：**

| Pending 原因 | 根因 | 修复操作 |
|---|---|---|
| Not enough job slot(s) | 队列/用户/节点槽位已满 | `bqueues -l <queue>` 查 MAX/NJOBS；`busers -u <user>` 查用户上限；等待释放或换队列 |
| Job requirements not satisfied | 资源请求无匹配节点 | 核对 `-R` 语法；`lsload -w` 查节点负载；放宽资源请求或换队列 |
| Not enough license(s) | License 不足 | `lmstat -a` 查 License 使用；等待释放或调整请求 |
| User job limit reached | 用户作业数/资源上限已达 | `blimits -u <user> -w` 查限制；`busers -u <user>` 查当前数；结束无用作业或申请提限 |
| Queue is closed/inactive | 队列已关闭/停用 | `bqueues` 查状态；换可用队列或联系管理员 |
| Dependency condition not satisfied | 依赖条件未满足 | `bjobs -l <job_id>` 查依赖规则；确认依赖作业是否完成 |
| Host group is empty | 目标主机组无可用节点 | `bhosts <host_group>` 查节点状态；换主机组或联系管理员 |

#### 状态 2：EXIT（作业异常退出/运行失败）

> 核心定位：是应用层错误，还是系统/调度层强制终止

**必做检查：**

1. 从 `bjobs -l` / `bhist -l` 提取 Exit Code 和 TERM_* 终止原因
2. 核对资源使用峰值 vs 限制阈值（MEMORY USAGE vs MEMLIMIT 等）
3. 查看作业 stdout/stderr 日志
4. 检查工作目录（CWD）权限和可访问性

**退出码规范：**

| 退出码范围 | 含义 | 排查方向 |
|---|---|---|
| 0 | 正常完成（DONE） | 无异常 |
| 1~127 | 应用程序自定义错误 | 查 stdout/stderr 日志；检查脚本语法、输入文件、依赖库 |
| ≥128 | 系统信号终止（信号 = 退出码 - 128） | 对照下方信号表定位根因 |

**高频信号退出码：**

| 退出码 | 信号 | 含义 | 最常见原因 |
|---|---|---|---|
| 130 | SIGINT (2) | 被 Ctrl+C 中断 | 用户手动中断 |
| 137 | SIGKILL (9) | 被强制杀死 | **内存溢出 OOM**（最常见） |
| 139 | SIGSEGV (11) | 段错误 | 内存越界、空指针、栈溢出 |
| 143 | SIGTERM (15) | 被优雅终止 | 管理员/调度器发送终止信号 |
| 255 | 信号 127 | 命令未找到 | 脚本无执行权限/节点无法连接 |

**TERM_* 终止原因全解：**

| 终止原因 | 根因 | 修复操作 |
|---|---|---|
| TERM_MEMLIMIT | 内存超限 | 增大 `-M` 限制；优化内存占用 |
| TERM_CPULIMIT | CPU 时间超限 | 增大 `-c` 限制；优化并行逻辑 |
| TERM_RUNLIMIT | 运行时长超限 | 增大 `-W` 限制；换支持更长时长的队列 |
| TERM_SWAP | Swap 超限 | 增大 Swap 限制；检查内存泄漏 |
| TERM_PROCESSLIMIT | 进程数超限 | 增大 `-p` 限制；优化并发数 |
| TERM_THREADLIMIT | 线程数超限 | 增大 `-T` 限制 |
| TERM_DISK_LIMIT | 磁盘超限 | 清理大文件；换更大存储分区 |
| TERM_ADMIN / TERM_FORCE_ADMIN | 管理员强制终止 | 联系管理员确认原因 |
| TERM_OWNER / TERM_FORCE_OWNER | 提交者本人终止 | 确认是否误操作 |
| TERM_CWD_NOTEXIST | 工作目录不可访问 | 确认 NFS 挂载正常；检查目录权限 |
| TERM_LOAD | 节点负载超阈值 | 换低负载节点；调整 `-R` 条件 |
| TERM_PREEMPT | 被高优先级作业抢占 | 提交到更高优先级队列 |
| TERM_WINDOW | 队列时间窗口关闭 | 等待窗口开启或换无限制队列 |
| TERM_EXTERNAL_SIGNAL | 外部信号终止 | 检查节点系统日志，确认是否被 OOM killer 杀死 |

#### 状态 3：RUN（作业运行中但卡住/无输出）

> 核心定位：是程序本身阻塞，还是系统/资源/网络问题

**必做检查：**

1. `bpeek <job_id>` — 确认程序是否有日志输出，是否卡在某步骤
2. `lsload <exec_host>` — 查看节点 CPU、内存、IO 负载
3. `bjobs -l <job_id>` — 查看资源使用是否已达上限
4. SSH 登录执行节点，`ps -u <user> -f | grep <job_id>` 查进程是否存活
5. `top -u <user>` 查进程状态，是否处于 D 状态（IO 阻塞）
6. 确认 NFS 挂载正常，工作目录和输入文件可访问
7. 确认执行节点的应用环境、依赖库、License 与提交节点一致

### Step 3：进阶排查

常规步骤无法定位时：

1. **调度系统日志**：联系管理员查看 mbatchd/sbatchd 日志
2. **节点系统日志**：查看 `/var/log/messages`，确认是否有 OOM killer、硬件故障
3. **作业重放测试**：`bsub -I` 交互式提交，实时复现问题
4. **资源限制测试**：逐步放宽资源限制，确认是否是阈值触发

### Step 4：诊断结论输出规范

诊断结果必须包含以下 4 部分：

```
### LSF 作业诊断报告

- 作业ID：<job_id>
- 作业状态：<PEND/EXIT/RUN>
- 提交用户：<user>
- 所属队列：<queue_name>
- 执行节点：<exec_host>
- 退出码/终止原因：<exit_code / TERM_*>

#### 一、根因分析
<基于命令输出的精准根因，禁止模糊描述>

#### 二、立即修复操作
<可直接复制执行的命令和步骤>

#### 三、长期预防建议
<可落地的规避方案，避免同类问题再次发生>

#### 四、补充说明（如需要）
<需管理员协助或额外日志排查的，明确说明>
```
