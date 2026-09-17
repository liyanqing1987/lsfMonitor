---
name: lsfmonitor_usage
description: 使用 lsfMonitor（bmonitor/bmonitor_cli）查询 LSF 集群作业/主机/队列与 EDA License 使用信息，支持实时查询、历史趋势、内存曲线、License 占用等结构化数据获取，是回答 LSF/License 运行状态与历史数据问题的核心数据工具
version: 4.0.0
tags:
  - bmonitor
  - bmonitor_cli
  - lsfmonitor
  - bsample
  - seedb
  - lsf 监控
  - license 监控
  - 作业查询
  - 作业历史
  - 内存曲线
  - 内存变化
  - mem 曲线
  - job 内存
  - 主机负载
  - 队列趋势
  - license 占用
  - license 使用
  - feature 使用
  - 利用率
  - pend
  - run
  - bjobs
  - bhist
  - bhosts
  - bqueues
  - lsload
  - lmstat
---

# lsfMonitor 数据查询技能（bmonitor_cli / bmonitor）

## 功能说明

lsfMonitor 提供两类工具用于回答 LSF 与 License 相关问题：

- **`bmonitor_cli`**（首选，AI 必用）：命令行工具，**stdout 输出纯 JSON**，直接在 Bash 中执行即可获取结构化数据，适合 AI 解析。覆盖实时状态查询与采样数据库的历史/趋势查询。
- **`bmonitor`**（GUI，仅用户要求看图时使用）：PyQt5 图形界面，支持用参数直达指定页（`-j/-u/-H/-f`），当用户需要**可视化图表**（例如"打开 GUI 看一下"、"弹出曲线窗口"）时启动，否则一律用 `bmonitor_cli`。

> **重要原则**：
> 1. 凡是能用 `bmonitor_cli` 拿到数据回答的问题，**不要启动 GUI**，直接在 Bash 里跑命令解析 JSON。
> 2. 用户明确要求"看图/打开 GUI/可视化"时才启动 `bmonitor`，并带上对应参数直达目标页。
> 3. `bmonitor_cli` 读不到或不够细的数据，再 fallback 到原生 `bjobs/bhist/bqueues/lsload/lmstat` 命令。
> 4. 作业 PEND/SLOW/FAIL 根因诊断优先使用专用技能 `lsf_job_pend / lsf_job_slow / lsf_job_fail`，本技能提供它们之外的数据（如历史内存曲线、License 历史占用）。

---

## 环境准备

调用工具前需保证 LSF 环境可用，且 `bmonitor_cli` / `bmonitor` 已在 PATH 中：

```bash
# 验证工具是否就绪
which bmonitor_cli    # 已安装则返回其路径
lsid                  # 确认 LSF 环境可用
```

lsfMonitor 是开源工具，安装方式见项目 README（通常 `python install.py` 后将生成的 wrapper 路径加入 PATH）。具体安装路径与 LSF 命令来源依各环境而异，不绑定特定 module 或路径。

`bmonitor_cli` 输出约定：
- **stdout**：仅 JSON（成功时为对象或数组；失败/无数据为 `{"error": "..."}`）
- **stderr**：日志/warning/错误提示，不影响 JSON 解析；脚本调用时建议 `2>/dev/null` 或忽略
- 有效命令返回码为 0（含 `{"error": "..."}` 结果）；无子命令或未知命令返回 1；调用方必须解析 JSON 并检查是否含 `error` 字段

---

## 触发条件

用户提问涉及以下内容时自动启用：

- 某个 job 的详细信息、内存使用、内存变化曲线（"job 12345 内存怎么变的"、"内存曲线"、"max mem 是多少"）
- 某个用户/队列/主机的作业列表、历史作业、作业统计
- 主机状态、负载（ut/内存/swap/io）趋势、利用率
- 队列排队/运行情况、趋势曲线
- License 谁在用、用了多少、feature 是否够用、license 到期时间、license 利用率趋势
- 集群整体情况（总览）、host group 信息
- 需要 GUI 可视化展示（"打开 monitor 看一下"、"弹窗"）

---

## AI 输出纪律

> 以下约束是给 AI 的判断与行为规则，不是给用户的科普。**除非用户行为触发，否则不要向用户复述这些约束本身**；AI 只在需要时把它体现为正确的行动或建议。

- **查询优先 `bmonitor_cli`**：能用 `bmonitor_cli`（JSON）拿到的数据，不要启动 GUI，也不要先堆裸 LSF 命令。仅当用户**明确要求看图/可视化**时才启动 `bmonitor` GUI 并带参数直达目标页。
- **RUN panel 权限按需说明**：RUN panel 跨主机执行需对目标主机 ssh 免密权限，普通用户通常无此权限。仅当用户**主动要跨主机批量查多台机器**时才提 RUN panel；无权限时说明普通用户无权 ssh 登录执行节点，改用 `bsub -q <queue> -m <exec_host> -Is "命令"` 在单台机器跑（机器未 closed、有 queue 权限时），或多用 LSF 指标。**不要建议无权限的普通用户用 RUN panel 或直接 ssh 登录执行节点。**
- **ANALYZE 报告按需引导**：用户要"生成报告/巡检集群"时引导用 ANALYZE（GUI AI→ANALYZE，或 `bsample -A` 生成 cluster 报告）；普通数据查询问题不复述 ANALYZE 能力。
- **基于实际数据下结论**：查询结果直接引用 JSON 字段值，不臆测；数据缺失时说明"无采样数据/需开 bsample crontab"，不编造。

---

## 工具速查

### 一、实时查询（走 LSF 命令 / lmstat，不需要采样数据）

| 用户问 | 命令 |
|-------|------|
| 集群版本/名称/master | `bmonitor_cli cluster-info` |
| **集群整体画像（一次拿全：主机状态分布 + slot/cpu/mem 利用率 + 作业计数 + per-queue 行 + 排队原因 Top + 活跃用户 Top）** | **`bmonitor_cli cluster-summary`** |
| **全集群排队原因 Top-N（聚合 `bjobs -u all -p`，归一化去重）** | **`bmonitor_cli pending-reasons [--top N]`** |
| host group 列表及成员 | `bmonitor_cli host-groups` |
| 单个作业详情 | `bmonitor_cli job <jobid>` |
| 诊断 job 为什么排队/慢/失败 | `bmonitor_cli job <jobid> --diagnose pend\|slow\|fail` |
| 当前作业列表（多条件可组合） | `bmonitor_cli jobs [--user X] [--status RUN/PEND/DONE/EXIT/PSUSP/USUSP/SSUSP] [--queue X] [--host X]` |
| 主机状态 | `bmonitor_cli hosts [--status ok/closed_Full/closed_Busy/closed_Adm/unavail/unreach] [--queue X] [--group X] [--sort-load]` |
| 队列总览（slot/pend/run） | `bmonitor_cli queues` |
| 单队列真实调度约束（RUNLIMIT/USERS/HOSTS/RES_REQ/SHARES）+ 成员主机 | `bmonitor_cli queue <queue>` |
| 用户级作业计数（谁在占 slot/排队最多） | `bmonitor_cli users [--sort RUN/NJOBS/PEND]` |
| 单主机调度负载详情（r15s/ut/mem/slots 等，= bhosts -l） | `bmonitor_cli host-detail <hostname>` |
| License feature 实时占用 | `bmonitor_cli license [--feature X] [--user X]`（模糊匹配，大小写不敏感） |
| License 到期时间 | `bmonitor_cli license-expires [--feature X]` |
| License 用户使用详情（含主机） | `bmonitor_cli license-usage [--user X]` |

> **宽问题首选 `cluster-summary` / `pending-reasons`**：回答"集群整体怎么样""全集群为什么在排队"这类宏观问题时，一次调用即可，无需 hosts+queues+users 各查一遍再自己算。

### 二、历史/趋势查询（读采样 SQLite 数据库，需要 crontab 采样已开启）

> 所有趋势类命令返回时序数组（`data`/`trend`/`load`/`usage` 字段，最多 100 个降采样点），可直接用于画曲线或分析趋势。

| 用户问 | 命令 |
|-------|------|
| 已采样集群/日期范围 | `bmonitor_cli db clusters` |
| **指定 job 的内存变化曲线（mem/idle_factor 时序）** | **`bmonitor_cli db job-mem <jobid> [--days N]`** |
| 主机历史负载曲线（ut/mem） | `bmonitor_cli host-load <hostname> [--days N]` |
| 主机利用率趋势（slot/cpu/mem） | `bmonitor_cli db host-util <hostname> [--days N] [--daily]` |
| **主机作业数趋势（判断某主机是否被作业压垮）** | **`bmonitor_cli db host-jobs <hostname> [--days N]`** |
| 队列 PEND/RUN 趋势 | `bmonitor_cli db queue <queue> [--days N]` |
| **队列成员主机历史（主机何时加入/离开队列，排查"队列成员变化导致 PEND"）** | **`bmonitor_cli db queue-hosts <queue> [--days N]`** |
| **主机组成员历史（组成员变化）** | **`bmonitor_cli db group-hosts <group> [--days N]`** |
| 历史已完成 job 详情（跨天查） | `bmonitor_cli db job <jobid>` |
| 历史完成作业列表（多维筛选） | `bmonitor_cli db jobs [--user X] [--status X] [--queue X] [--exit-code N] [--days N] [--date YYYYMMDD] [--limit N]` |
| 某用户历史作业汇总 | `bmonitor_cli db user <user> [--days N] [--date YYYYMMDD] [--limit N]` |
| 已采样 license server/vendor/feature | `bmonitor_cli db license-servers [--feature X]` |
| License 历史占用记录 | `bmonitor_cli db license-usage [--feature X] [--user X] [--days N]` |
| **License feature 利用率曲线** | **`bmonitor_cli db license-util --feature <X> [--days N] [--daily]`** |

### 三、GUI 启动（仅在用户要求可视化时使用）

| 用途 | 命令 |
|------|------|
| 默认打开（LSF JOBS 页） | `bmonitor` |
| 直接定位到某个作业（JOB 页，自动查询） | `bmonitor -j <jobid>` |
| 查某用户作业（JOBS 页） | `bmonitor -u <user> [<user> ...]` |
| 查主机负载曲线（LOAD 页） | `bmonitor -H <hostname> [<hostname> ...]` |
| 查某个 license feature（FEATURE 页，并联动 EXPIRES/USAGE/CURVE） | `bmonitor -f <feature> [<feature> ...]` |
| 多值同时查 | `bmonitor -u alice bob` / `bmonitor -H h1 h2 h3`（HOSTS 全填，LOAD 取首个并 warning）|
| 指定面板和子页 | `bmonitor -p lsf -t HOSTS` / `bmonitor -p license -t CURVE -f calibre` |
| License 面板（等价旧的 license_monitor） | `bmonitor -p license` |
| 暗黑模式 | 上述任意命令加 `-d` |

> **后台启动 GUI**：AI 在帮用户启动 GUI 时，务必加 `&` 让 GUI 脱离阻塞，如 `nohup bmonitor -j 12345 >/dev/null 2>&1 &`，并告诉用户 GUI 已打开。

---

## 典型场景与命令模板

### 场景 1：用户问"job 12345 的内存变化曲线 / 内存怎么跑的"

**这是最典型的需求，必须用 `db job-mem`**：

```bash
bmonitor_cli db job-mem 12345 --days 7
```

返回示例：
```json
{
  "jobid": "12345",
  "cluster": "IC_CLUSTER",
  "days": 7,
  "data_points": 42,
  "data": [
    {"sample_time": "20260831_100500", "mem": 45230.1, "idle_factor": 0.02},
    {"sample_time": "20260831_101000", "mem": 78120.5, "idle_factor": 0.15},
    ...
  ]
}
```

回答时：
- `mem` 单位与 LSF 一致（通常 MB）；可换算成 GB 告诉用户
- 可统计 `max(mem)`、平均内存、OOM 风险（如果采样点接近用户预留的 rusage_mem 或节点物理内存）
- 结合 `bmonitor_cli job 12345` 拿到 rusage_mem、status、exec_host 一起分析
- 如果 `data_points` 为 0 或返回 error，提示用户 job 可能未被采样（需 `bsample -m` 运行中，或 job 已过清理期）

### 场景 2：用户问"calibre license 现在还有吗/谁在用/够不够"

```bash
bmonitor_cli license --feature calibre
```

返回 feature 列表（含 issued/in_use/available/users 数组），可直接告诉用户总数、在用数、谁在用、还剩多少。

### 场景 3：用户问"calibre 最近一周的利用率/用得满吗"

```bash
bmonitor_cli db license-util --feature calibre --days 7
```

返回每个 vendor 下该 feature 的时序 `{sample_time, issued, in_use, utilization}`，可统计：
- 峰值利用率（max(utilization)）
- 平均利用率
- 达到 100% 的时段（license 不够用的时间点）

### 场景 4：用户问"我最近 3 天的作业有哪些失败的"

```bash
bmonitor_cli db jobs --user <当前用户> --status EXIT --days 3
```

结合 `bmonitor_cli job <jobid> --diagnose fail` 进一步诊断每个失败 job。

### 场景 5：用户问"n019-123-001 这台机器最近负载/内存怎么样"

```bash
bmonitor_cli host-load n019-123-001 --days 7      # ut/mem 时序
bmonitor_cli db host-util n019-123-001 --days 7   # slot/cpu/mem 利用率
```

### 场景 6：用户问"normal 队列排队情况"

```bash
bmonitor_cli queues                       # 实时
bmonitor_cli db queue normal --days 1     # PEND/RUN 趋势
```

### 场景 7：用户要求"打开 GUI 看一下 job 12345"

```bash
nohup bmonitor -j 12345 >/dev/null 2>&1 &
```

告诉用户已在 GUI 里打开了该作业详情页。

### 场景 8：宏观 / 集群级问题（首选一站式，不要拆分查询再自己算）

```bash
bmonitor_cli cluster-summary             # 一次拿全：主机分布+利用率+作业计数+per-queue+排队原因Top+活跃用户Top
bmonitor_cli pending-reasons --top 20    # 全集群排队原因 Top-20（聚合去重）
```

无需 hosts+queues+users 各查一遍再自己算。`cluster-summary` 的数据与 AI 集群分析报告的权威指标完全一致。

---

## 输出 JSON 关键字段速查

### 实时 `job <jobid>`

核心字段：`jobid, job_name, user, status, queue, project, from_host, exec_host, started_time, finished_time, cwd, command, processors_requested, cpu_time, rusage_mem, mem, max_mem, avg_mem, idle_factor, exit_code, term_signal, interactive_mode`

### 诊断 `job <jobid> --diagnose X`

- **pend**：`pending_reasons, queue_max_slots, queue_running, queue_pending, requested_resources`
- **slow**：`idle_factor, cpu_time, mem, rusage_mem, max_mem, started_on, started_time, host_status, host_ut, host_mem`
- **fail**：`exit_code, term_signal, finished_time, max_mem, rusage_mem, run_limit, cpu_time, exit_code_reason, term_signal_reason, possible_cause`

### 趋势类（曲线数据）

统一包含 `sample_time`（字符串 `YYYYMMDD_HHMMSS`，`--daily` 时为 `YYYYMMDD`）和对应指标字段：

| 命令 | 数组键 | 指标字段 |
|------|--------|---------|
| `host-load` | `load` | `ut, mem` |
| `db job-mem` | `data` | `mem, idle_factor` |
| `db queue` | `trend` | `TOTAL, NJOBS, PEND, RUN, SUSP` |
| `db host-jobs` | `trend` | `NJOBS, RUN, SSUSP, USUSP` |
| `db host-util` | `trend` | `slot, cpu, mem` |
| `db license-util` | `features[].trend` | `issued, in_use, utilization`（utilization 为百分比 0-100） |

> 趋势数组按 `sample_time` 升序排列；数据点数由命令自动降采样至 ≤100。

---

## 与其他技能的协作

| 场景 | 本技能提供 | 专用技能 |
|------|-----------|---------|
| 作业 PEND 排队原因 | `bmonitor_cli job <id> --diagnose pend`、`bmonitor_cli queues`、`db queue <q>` | `lsf_job_pend` 做根因诊断与修复建议 |
| 作业跑得慢 | `bmonitor_cli job <id> --diagnose slow`、`db job-mem <id>`（内存/cpu 曲线）、`host-load <host>` | `lsf_job_slow` 做根因诊断 |
| 作业失败/退出 | `bmonitor_cli job <id> --diagnose fail`、`db jobs --status EXIT` | `lsf_job_fail` 做退出码/信号深度解读 |
| EDA License 问题（不够用/谁占着） | `license`、`license-usage`、`db license-util` | 可配合 `eda_license` 技能 |
| LSF 基础命令排障 | 作为补充数据源 | 可直接调用 `bjobs/bhist/bqueues/bhosts/lsload/lmstat` 交叉验证 |

---

## 跨主机批量运维（RUN panel）

RUN panel 能跨主机并行执行 shell 命令（ssh+pexpect，per-host 超时，按 Queue/Group 筛选主机），适合**一次查多台机器**的运维场景（需 ssh 权限，见顶部"AI 输出纪律"）：

| 场景 | 做法 |
|------|------|
| normal 队列所有主机是否磁盘满 | RUN panel 选 Queue=normal，跑 `df -h | grep -E '9[0-9]%\|100%'` |
| IC_ETX 组机器最近有无 OOM | RUN panel 选 Group=IC_ETX，跑 `dmesg | grep -i oom | tail -20` |
| 批量查某组机器 dmesg / 进程 / 负载 | RUN panel 选对应 Group，跑对应命令 |

- RUN panel 是 **GUI 功能**（`bmonitor -p run` 打开），无命令行入口；AI 可代为启动 GUI：`nohup bmonitor -p run >/dev/null 2>&1 &`，并在面板里配置 Queue/Group 后执行。
- 节点已 closed/unavail 时无法 ssh，结果为 FAIL/UNREACH，改用 LSF 指标判断。
- **单机替代方案**：普通用户无 ssh 权限时，可用 `bsub -q <queue> -m <exec_host> -Is "命令"` 在单台执行节点上跑命令（机器未 closed、有 queue 权限时）。

---

## AI 健康分析报告（ANALYZE）

lsfMonitor 的 AI panel 能生成四种**一次性**健康报告（非对话式问答，是自包含 HTML 报告），适合**主动巡检**而非被动查询：

| 报告类型 | 覆盖 | 触发方式 |
|---------|------|---------|
| cluster | 集群整体（主机/队列/作业/排队/利用率） | GUI AI→ANALYZE→CLUSTER，或 `bsample -A` |
| user | 单用户作业（PEND/SLOW/FAIL 模式 + 内存浪费） | GUI AI→ANALYZE→USER |
| job | 单作业深度（状态分析 + 根因 + 修复建议） | GUI AI→ANALYZE→JOB |
| queue | 单队列负载/资源充裕度 | GUI AI→ANALYZE→QUEUE |

- 用户要"生成一份报告/巡检一下集群"时，引导用 ANALYZE（GUI）或 `bsample -A`（cluster 报告）。
- 与本 skill 的查询不同：ANALYZE 是 AI 综合多源数据**生成结论性报告**，本 skill 是**取数据让 AI 现场回答**。两者互补：取数用 bmonitor_cli，要正式报告用 ANALYZE。

---

## 异常处理

- 返回 `{"error": "No data ..."}` / `{"error": "Database not found"}`：说明采样 crontab 未开或数据已被清理，告诉用户需要先部署 `bsample/license_sample` 定时采样；实时类命令出现 error 通常是 LSF 环境问题（先确认 `lsid`、`bjobs` 能直接跑）。
- stderr 输出 `*Warning*: ...` 是工具内部日志，不影响 stdout JSON，可以忽略或展示给用户。
- GUI 启动失败：先让用户检查 X11/DISPLAY 环境变量（`echo $DISPLAY`），远程 SSH 需要 X 转发或 VNC。
- `bmonitor_cli` 不存在：确认 lsfMonitor 已安装（见 README）且其 wrapper 路径已在 PATH 中；若已安装仍找不到，说明版本过旧，升级后使用。

---

## 辅助工具（仅在 bmonitor_cli 不满足时使用）

| 工具 | 用途 |
|------|------|
| `seedb` | 直接查询 lsfMonitor 的 SQLite 数据库（表结构/SQL 自由查询），当 bmonitor_cli 没有封装你需要的维度时使用 |
| `akill` | 批量 kill 作业（多条件：jobid 范围/用户/队列/状态） |
| `bsample` | 手动触发一次采样（调试用，正常走 crontab） |
| `license_sample` | 手动触发 license 采样 |
| `check_issue_reason` | GUI 版作业问题诊断（PEND/SLOW/FAIL），仅用户要求 GUI 时启动 |

> 这些工具非 AI 回答问题的首选，`bmonitor_cli` 能覆盖的场景一律用 `bmonitor_cli`。
