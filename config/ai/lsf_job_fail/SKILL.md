---
name: lsf_job_fail
description: 诊断LSF/OpenLava/Volclava作业EXIT失败问题，通过退出码和TERM_*终止原因精准定位根因，区分应用错误与系统终止
version: 1.0.0
tags:
  - exit
  - fail
  - 失败
  - 退出码
  - exit code
  - killed
  - 被杀
  - terminated
  - 终止
  - term_
  - signal
  - oom
  - 内存溢出
  - segfault
  - core dump
  - 崩溃
---

# LSF 作业 EXIT/失败诊断技能

## 功能说明

专项诊断作业 EXIT（异常退出/运行失败）问题，覆盖：

- 应用程序自身错误（脚本语法、输入文件、依赖库缺失）
- 系统信号终止（OOM、段错误、管理员/调度器 kill）
- 资源超限终止（内存/CPU时间/运行时长/进程数/Swap/磁盘）
- 环境问题（工作目录不可访问、NFS 挂载异常）
- 被抢占或调度器策略终止

## 触发条件

用户提问涉及以下内容时自动启用：

- 作业 EXIT / 失败了，怎么查原因
- 退出码 137/139/143/255 是什么意思
- 作业被杀了 / 终止了
- TERM_MEMLIMIT / TERM_RUNLIMIT 等
- 怎么看已完成作业的日志 / 历史记录

---

## AI 输出纪律

> 以下约束是给 AI 的判断与行为规则，不是给用户的科普。**除非用户行为触发，否则不要向用户复述这些约束本身**；AI 只在需要时把它体现为正确的行动或修复建议。

- **集群约束不主动科普**：本集群内存类用 `-R "rusage[mem=...]"`（MB 单位，不支持 `G` 后缀），不推荐 `-M`。仅当用户**自己已使用 `-M`** 时才提醒"建议去掉 `-M` 改用 `-R rusage[mem=...]`"；用户没提 `-M` 时，AI 内部知道别建议 `-M` 即可，不主动解释"`-M` 不推荐"。
- **权限约束按需说明**：普通用户无权 ssh 登录执行节点。仅当用户**主动要求登执行节点看进程/日志**时才说明"无 ssh 权限，改用 LSF 指标或请管理员协助"；用户没问节点登录时不提。
- **节点级排查用 `bsub -m` 替代 ssh**：需要看执行节点的系统日志/进程时，用 `bsub -q <queue> -m <exec_host> -Is "命令"` 在指定机器跑（机器未 closed、有 queue 权限时）；机器 closed/无权限时转 LSF 指标或请管理员。不要建议用户直接 ssh 登录执行节点。
- **修复操作走确认流程**：诊断后提出的涉及状态变更的命令（如 bkill/bmod/重新 bsub）属"诊断后修复"，按主 system prompt 走"可选操作编号列表 + 用户选号确认"流程，不要直接执行，也**不要向用户解释"这是危险命令所以我要你确认"**——照流程做即可。
- **基于实际数据下结论**：退出码/TERM 的判断必须引用具体数字（如 max_mem=X / MEMLIMIT=Y），不臆测；推测用"可能/疑似"。

---

## 诊断流程

**执行要求：**
1. 所有诊断命令必须通过 run_command 工具直接执行并分析结果，不要将命令以文本形式输出给用户。
2. 如果用户询问的是新的作业ID或距上次查询已有一段时间，必须重新执行命令获取实时数据。
3. 每次诊断必须至少执行 Step 1 的命令，不可跳过。

### Step 0：前置环境检查（严格执行，不要跳过或询问用户）

1. 确认当前用户身份（从 system prompt 中的 Current user 获取）
2. 如果用户没有提供 Job ID，直接运行 `bmonitor_cli jobs --user <current_user> --status EXIT --limit 20` 查看最近的 EXIT 作业（输出 JSON，跨 LSF/OpenLava/Volclava 稳定）
3. 找到 EXIT 作业后直接进入 Step 1 诊断，不要反问用户确认

### Step 1：采集 EXIT 作业信息

优先用 `bmonitor_cli`（JSON 输出、跨集群稳定），不足时再用裸 LSF 命令补：

```bash
bmonitor_cli job <job_id> --diagnose fail    # 首选：聚合退出码/TERM/资源峰值/历史记录
bjobs -l <job_id>                            # 补充：作业仍在 bjobs 中（通常 EXIT 后 1 小时内），取 stdout/stderr 路径等细节
bmonitor_cli db job <job_id>                 # 作业已从 bjobs 消失时用此替代 bhist（从采样 DB 读已完成作业，免 bhist 超时）
bhist -l <job_id>                            # 仅当 bmonitor_cli db job 无数据时才用；大集群历史作业 bhist 可能慢或超时
```

**必须提取的核心字段：**

- Exit Code、Termination Reason（TERM_*）
- 资源使用峰值：MAX MEM、CPU TIME USED
- 资源限制阈值：MEMLIMIT、RUNLIMIT、CPULIMIT
- 工作目录（CWD）、执行命令、stdout/stderr 路径、执行节点

### Step 2：根据 Exit Code 分支诊断

#### 2.1 Exit Code 1~127：应用程序错误

stdout/stderr 路径从 Step 1 的 `bjobs -l` 输出（OUTPUT/ERROR 字段）或 CWD 下 `.log/.err/.out` 文件获取，不要臆测路径：

```bash
cat <error_file_path> | tail -50    # 查看 stderr 最后 50 行
cat <output_file_path> | tail -50   # 查看 stdout 最后 50 行
ls -la <script_path>                # 检查脚本权限
```

| 退出码 | 常见含义 | 排查方向 |
|---|---|---|
| 1 | 通用错误 | 查 stderr |
| 2 | shell 命令误用 | 检查脚本语法 |
| 126 | 命令不可执行 | 检查文件权限 |
| 127 | 命令未找到 | 检查 PATH、模块加载 |

#### 2.2 Exit Code >= 128：系统信号终止

信号编号 = Exit Code - 128

| 退出码 | 信号 | 含义 | 最常见原因 |
|---|---|---|---|
| 130 | SIGINT (2) | Ctrl+C 中断 | 用户手动中断 |
| 131 | SIGQUIT (3) | 退出+core dump | 程序异常 |
| 134 | SIGABRT (6) | 异常终止 | 断言失败 |
| 137 | SIGKILL (9) | 被强制杀死 | **内存溢出 OOM** |
| 139 | SIGSEGV (11) | 段错误 | 内存越界、空指针 |
| 143 | SIGTERM (15) | 被优雅终止 | 管理员/调度器终止 |
| 152 | SIGXCPU (24) | CPU 时间超限 | 超过 CPULIMIT |

**Exit Code 137 深度排查：**
- MAX MEM > MEMLIMIT → LSF 杀死
- MAX MEM ≈ 节点物理内存 → 系统 OOM killer
- MAX MEM 远低于 MEMLIMIT → 其他进程竞争内存

**Exit Code 255（非信号，单独说明）：**
不是信号终止（128+signum 体系之外），通常是 LSF 在**作业未正常跑起来**或 shell 层执行失败时填的码。常见原因：
- 执行节点不可达 / SSH 提交失败 / 节点失联
- 可执行文件缺失、权限问题、PATH/模块未加载（与 127 类似但由 LSF 包装层返回）
- bsub 阶段或环境初始化阶段异常
排查：先确认节点是否可达、可执行路径是否存在；查 `bjobs -l` 的 SUSPENSION/PENDING REASON 与 bhist 事件。

### Step 3：TERM_* 终止原因诊断

| 终止原因 | 根因 | 修复操作 |
|---|---|---|
| TERM_MEMLIMIT | 内存超限 | 增大 `-R "rusage[mem=...]"`（MB）；优化内存占用 |
| TERM_CPULIMIT | CPU 时间超限 | 增大 `-c` 限制；优化并行逻辑 |
| TERM_RUNLIMIT | 运行时长超限 | 增大 `-W` 限制；换长时间队列 |
| TERM_SWAP | Swap 超限 | 增大 Swap 限制；排查内存泄漏 |
| TERM_PROCESSLIMIT | 进程数超限 | 增大 `-p` 限制 |
| TERM_THREADLIMIT | 线程数超限 | 增大 `-T` 限制 |
| TERM_DISK_LIMIT | 磁盘超限 | 清理大文件；换大分区 |
| TERM_ADMIN / TERM_FORCE_ADMIN | 管理员终止 | 联系管理员确认原因 |
| TERM_OWNER / TERM_FORCE_OWNER | 用户自己终止 | 确认是否误操作 |
| TERM_CWD_NOTEXIST | 工作目录不可访问 | 确认 NFS 挂载；修正路径 |
| TERM_LOAD | 节点负载超阈值 | 换低负载节点 |
| TERM_PREEMPT | 被高优先级作业抢占 | 提交到更高优先级队列 |
| TERM_WINDOW | 队列时间窗口关闭 | 换无限制队列 |
| TERM_EXTERNAL_SIGNAL | 外部信号 | 查节点系统日志，通常是 OOM killer |

### Step 4：进阶排查

1. **查看完整日志**：如果 stdout/stderr 路径已知，查看完整内容
2. **检查执行环境一致性**：`module list`、`env | grep PATH`
3. **节点系统日志**：查 OOM 等，可用 `bsub -q <queue> -m <exec_host> -Is "dmesg | grep -i oom | tail -20"` 在执行节点跑（机器未 closed、有权限时）；无权限时联系管理员查看 `/var/log/messages`
4. **重放测试**：`bsub -I <command>` 交互式提交复现

### Step 5：诊断结论输出规范

```
### EXIT 诊断报告

- 作业ID：<job_id>
- 提交用户：<user>
- 所属队列：<queue_name>
- 执行节点：<exec_host>
- 退出码：<exit_code>
- 终止原因：<TERM_* 或无>

#### 一、根因分析
<基于命令输出的精准根因，引用具体数字>

#### 二、立即修复操作
<可直接复制执行的命令>

#### 三、长期预防建议
<可落地的规避方案>
```
