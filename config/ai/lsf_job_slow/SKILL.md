---
name: lsf_job_slow
description: 诊断LSF/OpenLava/Volclava作业RUN状态但卡住、无输出、运行缓慢或无响应的问题，定位是程序阻塞还是系统/资源/网络瓶颈
version: 1.0.0
tags:
  - stuck
  - 卡住
  - 无输出
  - no output
  - 慢
  - slow
  - hang
  - 挂起
  - 无响应
  - 不动
  - 跑不动
  - 运行慢
  - 阻塞
  - 长时间运行
---

# LSF 作业 RUN 卡住/缓慢诊断技能

## 功能说明

专项诊断作业处于 RUN 状态但表现异常的问题，覆盖：

- **License 等待（最常见的"卡住不动"原因）**：主进程阻塞在 license checkout 上，CPU 不动、无输出，看似卡死实则在等 license 释放
- 作业运行中但无任何输出 / 速度异常缓慢 / 没有进展
- 进程处于 D 状态（不可中断的 IO 等待）
- NFS/网络存储挂载异常导致程序阻塞
- 执行节点负载过高导致争抢资源
- 死锁（多进程/多线程程序）

## 触发条件

用户提问涉及以下内容时自动启用：

- 作业 RUN 状态但卡住了 / 没输出 / 没进展
- 作业跑了很久还没结束 / 比预期慢很多
- 怎么看作业跑到哪了
- 作业是不是挂了 / 死了

---

## AI 输出纪律

> 以下约束是给 AI 的判断与行为规则，不是给用户的科普。**除非用户行为触发，否则不要向用户复述这些约束本身**；AI 只在需要时把它体现为正确的行动或修复建议。

- **License 等待是 slow 首要怀疑对象**：作业 idle_factor 低、"看上去不动"时，优先排查 license 等待（看日志 license 关键词 + `bmonitor_cli license` 查 free=0 + `license-usage` 查占用者），而非先怀疑死锁/IO。这是本技能最重要的判断原则。
- **集群约束不主动科普**：本集群内存类用 `-R "rusage[mem=...]"`（MB 单位，不支持 `G` 后缀），不推荐 `-M`。仅当用户**自己已使用 `-M`** 时才提醒改用 `-R`；用户没提时不主动解释。
- **节点级排查用 `bsub -m` 替代 ssh**：普通用户通常无 ssh 登录执行节点权限，需要看节点进程/磁盘时用 `bsub -q <queue> -m <exec_host> -Is "命令"` 在指定机器跑（机器未 closed、有 queue 权限时）。仅当用户**需要看节点级信息**时才给此方案；机器 closed/无权限时转 LSF 指标（idle_factor + `bmonitor_cli host-detail`）或请管理员。不要建议用户直接 ssh 登录执行节点。
- **修复操作走确认流程**：诊断后提出的状态变更命令（如 bkill/bresume/重新 bsub）按主 system prompt 走"可选操作编号列表 + 用户选号确认"流程，不直接执行，也**不向用户解释"这是危险命令所以我要你确认"**。
- **基于实际数据下结论**：卡住/慢的判断必须引用具体数字（idle_factor 值、cpu_time、max_mem、ut/io 等），不臆测；推测用"可能/疑似"。

---

## 诊断流程

**执行要求：**
1. 所有诊断命令必须通过 run_command 工具直接执行并分析结果，不要将命令以文本形式输出给用户。
2. 如果用户询问的是新的作业ID或距上次查询已有一段时间，必须重新执行命令获取实时数据。
3. 每次诊断必须至少执行 Step 1 的命令，不可跳过。

### Step 0：前置环境检查（严格执行，不要跳过或询问用户）

1. 确认当前用户身份（从 system prompt 中的 Current user 获取）
2. 如果用户没有提供 Job ID，直接运行 `bmonitor_cli jobs --user <current_user> --status RUN --limit 20` 列出所有 RUN 作业（输出 JSON，跨 LSF/OpenLava/Volclava 稳定）
3. 找到作业后直接进入 Step 1 诊断，不要反问用户确认

### Step 1：采集运行状态信息

优先用 `bmonitor_cli`（JSON 输出、跨集群稳定），不足时再用裸 LSF 命令补：

```bash
bmonitor_cli job <job_id> --diagnose slow    # 首选：聚合 idle_factor + CPU/内存 + 主机负载(ut/mem)
bmonitor_cli job-output <job_id>               # 首选：实时 stdout/stderr（= bpeek，第一手线索）
bjobs -l <job_id>                             # 补充：全量信息——执行节点、资源使用、运行时长细节
bpeek <job_id>                                # 补充：bmonitor_cli job-output 失败时用
```

**必须提取的核心字段：**

- 执行节点、运行时长、CPU TIME、MAX MEM
- **idle_factor（cputime/runtime，诊断"慢"的核心指标）**
- RUNLIMIT、MEMLIMIT
- 工作目录、执行命令、输出文件路径

**idle_factor 判断（不要用单一硬阈值）：**
- idle_factor 高（接近 1）→ CPU 一直忙，作业在真正计算，可能只是负载高/数据量大
- idle_factor 低（如 < 0.3）→ CPU 大量空闲，作业"看上去不动"。**首要怀疑 License 等待**（主进程阻塞在 license checkout，见 Step 5.1），其次才是 IO/NFS 故障、锁/死锁、网络等待
- idle_factor 结合 run_time、cpu_time、max_mem vs requested mem、slots 综合判断，解释推理过程

**bpeek 关键判断：**
- 有持续输出 → 作业在运行，可能只是慢
- 输出停在某一步 → 定位到具体阻塞点
- 完全无输出 → 可能在初始化，或确实卡死

### Step 2：检查执行节点状态

```bash
bmonitor_cli host-detail <exec_host>     # 首选：调度负载详情（r15s/ut/mem/slots 等，= bhosts -l）
bmonitor_cli host-load <exec_host> --days N   # 首选：历史负载时序（看趋势，job 运行 N 天则 --days N）
# 不足时补充：
lsload <exec_host>                       # 补：节点实时负载：CPU、内存、IO、Swap
bhosts -l <exec_host>                    # 补：节点作业数、状态
```

**异常判断标准：**

| 指标 | 异常判断 |
|---|---|
| r15m (负载) | 远超 CPU 核数 → 节点过载 |
| ut (CPU利用率) | 持续 100% → CPU 争抢 |
| mem (可用内存) | 接近 0 → 内存耗尽，频繁 Swap |
| io (IO等待) | 持续 > 30% → IO 瓶颈 |
| tmp (临时磁盘) | 接近 0 → 磁盘满 |

### Step 3：判断是否卡在 IO / 节点级诊断

**首选：用 LSF 自带指标间接判断（无需 ssh，推荐）：**

| 现象 | LSF 指标判断 |
|---|---|
| 进程一直等 IO（疑似 D 状态） | idle_factor 很低（CPU 大量空闲）+ 节点 io 高（`bmonitor_cli host-detail` 的 io 字段）→ 可能在等 NFS/磁盘 IO |
| CPU 真忙在算 | idle_factor 高 + ut 高 → 节点在算，非卡住 |
| 僵尸/被暂停 | `bmonitor_cli job <job_id>` 的 STATUS 非 RUN（ZOMBIE/SUSP 等）|

**需要看执行节点进程/磁盘时（替代 ssh 的可行方案）：** 普通用户通常无 ssh 登录执行节点权限，但可用 `bsub -m <exec_host>` 让 LSF 在指定机器上跑命令（需有 queue 提交权限、机器未 closed）：

```bash
# 在 exec_host 上查自己作业的进程状态（-Is 交互式实时看输出）
bsub -q <queue> -m <exec_host> -Is "ps -u <user> -o pid,stat,pcpu,pmem,etime,cmd"
# 查执行节点磁盘/NFS 是否正常
bsub -q <queue> -m <exec_host> -Is "df -h <cwd>"
bsub -q <queue> -m <exec_host> -Is "ls <cwd>"
```

进程状态含义：

| 进程状态 | 含义 | 处理方式 |
|---|---|---|
| R | 运行中 | 正常 |
| S | 可中断睡眠 | 正常，等待 IO/事件 |
| D | 不可中断睡眠 | **NFS 挂载卡住或磁盘 IO 故障** |
| Z | 僵尸进程 | 父进程未回收子进程 |
| T | 停止 | 可能被 `bstop` 暂停 |

> `bsub -m` 限制：命令要排队等调度；机器 closed/unavail 时无法用（改用上表 LSF 指标间接判断，或请管理员协助）；节点环境可能需重新 `module load`。

### Step 4：检查应用日志

从 `bjobs -l` 获取 CWD 和 stdout/stderr 路径，检查应用自身的日志输出。

> CWD 通常是 NFS 共享目录（家目录/项目目录），本机可直接访问。若 CWD 在执行节点本地盘，本机访问不到时可用 `bsub -q <queue> -m <exec_host> -Is "ls -lt <cwd> | head"` 在执行节点上查（机器未 closed、有 queue 权限时）。

```bash
ls -lt <cwd>/*.log 2>/dev/null | head -10    # 查看 CWD 下最近的日志文件
tail -50 <stderr_file>                       # 查看 stderr 最新内容
tail -50 <stdout_file>                       # 查看 stdout 最新内容
ls -lt <cwd> | head -20                      # 查看 CWD 下最近修改的文件
```

**关注的异常模式：**
- 大量重复的 warning/error 行 → 程序在反复重试某个失败操作
- "timeout"/"connection refused"/"retry" → 等待外部服务/网络资源
- "waiting for license"/"license unavailable" / "FATAL... checkout failed" → **应用级 License 等待（慢的常见主因，见 Step 5.1）**
- 日志文件长时间未更新（`ls -l` 看 mtime）→ 程序卡在某处
- 日志文件增长极快（`du -h`）→ 可能在刷大量无用输出，IO 瓶颈
- "segfault"/"assertion"/"exception" → 程序虽未退出但已进入异常状态

### Step 5：分场景诊断

#### 5.1 输出停在某步骤 / 看上去卡住不动 —— **优先排查 License 等待**

> **License 等待是 job slow 最常见的原因之一**：很多作业主进程在启动后或运行中需要 checkout license，若 license 被占满，主进程会**阻塞在 license checkout 上，CPU 不动、无输出，看上去就是"卡死"**。这种"卡住"不是死锁，而是等 license 释放。诊断 RUN 作业卡住时**必须把 license 等待作为首要怀疑对象之一**。

排查步骤：
1. 先看应用日志是否出现 "waiting for license"/"license unavailable"/"checkout failed"/"FATAL" 等字样（见 Step 4）→ 出现即可确认是 license 等待
2. 用 `bmonitor_cli license --feature <feature>` 或 query_license_info 查对应 feature 的 issued/in_use/available，确认 free=0（被占满）
3. 用 `bmonitor_cli license-usage --feature <feature>` 查谁在占用，判断是否有人长时间持有不放
4. 确认后给出建议：等占用量下降、联系占用者、或检查是否有 license server 异常

其它可能：
- 是否在等输入（交互式程序在 batch 模式等 stdin）
- 是否死锁（多线程/多进程程序，idle_factor 低但无 license/IO 问题，需管理员协助看节点进程）

#### 5.2 完全无输出
```bash
bmonitor_cli job <job_id>    # 间隔 30 秒查两次，比较 CPU TIME 变化
```
- CPU TIME 增长 → 程序在运行，输出被缓冲
- CPU TIME 不变 → 确实卡住（IO 等待或死锁）
- MEM 持续增长 → 在加载数据

#### 5.3 运行缓慢
```bash
bmonitor_cli hosts --status ok --sort-load        # 首选：节点负载排序
bmonitor_cli jobs --host <exec_host> --status RUN # 首选：同节点有多少其他作业
# 不足时补充：
lsload <exec_host>          # 补：节点是否过载
bjobs -u all -m <exec_host>  # 补：同节点其他作业（旧命令）
```
- idle_factor 低 → 多半在等 License / IO / 锁，**优先按 Step 5.1 排查 License 等待**，再结合日志定位 IO/锁
- 节点过载 → 建议换节点重提交
- Swap 使用高 → 内存不足导致频繁换页
- 应用日志有大量 retry/warning → command 本身有问题

#### 5.4 作业被暂停
```bash
bmonitor_cli job <job_id>    # 首选：查 STATUS 是否为 SUSP
bjobs -l <job_id>            # 补：查看 SUSPEND 原因
```
- USUSP → 用户 `bstop` 了，可用 `bresume` 恢复（按顶部"修复操作走确认流程"执行）
- SSUSP → 系统暂停，等待负载下降或联系管理员

### Step 6：进阶排查

1. **strace 跟踪**：`strace -p <pid> -e trace=network,file -f 2>&1 | head -50` —— 需在 exec_host 上执行，可用 `bsub -q <queue> -m <exec_host> -Is "strace -p <pid> ..."` 跑（机器未 closed、有 queue 权限时）；机器 closed/无权限时无法用，转 LSF 指标判断 + 请管理员协助
2. **对比运行**：`bsub -I -m <host> <command>` 交互式复现（提交到同一台机器跑，观察是否复现问题；机器已 closed/不可达时无法用）
3. **监控变化**：间隔 30 秒两次 `bmonitor_cli job <job_id>` 或 `bjobs -l`，比较 CPU TIME/MEM 是否增长

### Step 7：诊断结论输出规范

```
### RUN 卡住/缓慢诊断报告

- 作业ID：<job_id>
- 执行节点：<exec_host>
- 已运行时长：<duration>
- 当前资源使用：CPU=<cpu_time>, MEM=<max_mem>

#### 一、根因分析
<基于命令输出的精准根因>

#### 二、立即修复操作
<kill 并重提交？等待？切换节点？>

#### 三、长期预防建议
<避免类似问题的提交策略>
```
