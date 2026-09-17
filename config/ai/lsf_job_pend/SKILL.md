---
name: lsf_job_pend
description: 诊断LSF/OpenLava/Volclava作业PEND排队问题，定位调度器为何不分配资源，给出可执行的修复方案
version: 1.0.0
tags:
  - pending
  - pend
  - 排队
  - 不跑
  - 不启动
  - 等待
  - waiting
  - 挂起
  - 调度
---

# LSF 作业 PEND 诊断技能

## 功能说明

专项诊断作业 PEND（排队未启动）问题，覆盖：

- 队列/用户/节点槽位已满
- 资源请求无匹配节点
- License 不足
- 用户作业数/资源上限已达
- 队列已关闭/停用
- 依赖条件未满足
- 目标主机组无可用节点
- 公平共享调度（Fairshare）被降权
- 预约资源（Reservation）被抢占

## 触发条件

用户提问涉及以下内容时自动启用：

- 作业为什么 PEND / 一直不跑 / 排队不动
- 作业提交后没启动
- 等了很久还在排队
- 为什么调度器不给我分配资源

---

## AI 输出纪律

> 以下约束是给 AI 的判断与行为规则，不是给用户的科普。**除非用户行为触发，否则不要向用户复述这些约束本身**；AI 只在需要时把它体现为正确的行动或修复建议。

- **集群约束不主动科普**：本集群内存类用 `-R "rusage[mem=...]"`（MB 单位，不支持 `G` 后缀），不推荐 `-M`。仅当用户**自己已使用 `-M`** 时才提醒"建议去掉 `-M` 改用 `-R rusage[mem=...]`"；用户没提 `-M` 时不主动解释。
- **权限约束按需说明**：普通用户无 ssh 登录执行节点权限，但可用 `bsub -q <queue> -m <exec_host> -Is "命令"` 在指定机器上跑命令（机器未 closed、有 queue 权限时）。仅当用户**需要看节点级信息**时才给此方案；机器 closed/无权限时转 LSF 指标或请管理员。
- **修复操作走确认流程**：诊断后提出的涉及状态变更的命令（如 bkill/bmod/bresume/重新 bsub）按主 system prompt 走"可选操作编号列表 + 用户选号确认"流程，不直接执行，也**不向用户解释"这是危险命令所以我要你确认"**。
- **基于实际数据下结论**：排队根因必须引用具体数字（如 NJOBS=100/MAX=100、pending reason 原文），不臆测；推测用"可能/疑似"。

---

## 诊断流程

**执行要求：**
1. 所有诊断命令必须通过 run_command 工具直接执行并分析结果，不要将命令以文本形式输出给用户。
2. 如果用户询问的是新的作业ID或距上次查询已有一段时间，必须重新执行命令获取实时数据。
3. 每次诊断必须至少执行 Step 1 的命令，不可跳过。

### Step 0：前置环境检查（严格执行，不要跳过或询问用户）

1. 确认当前用户身份（从 system prompt 中的 Current user 获取）
2. 如果用户没有提供 Job ID，直接运行 `bmonitor_cli jobs --user <current_user> --status PEND --limit 20` 列出所有 PEND 作业（输出 JSON，跨 LSF/OpenLava/Volclava 稳定）
3. 如果只有 1 个 PEND 作业，直接进入 Step 1 诊断
4. 如果有多个 PEND 作业，逐一对每个作业执行 Step 1 诊断，一次性给出所有结果

### Step 1：采集 PEND 作业信息

优先用 `bmonitor_cli`（JSON 输出、跨集群稳定），不足时再用裸 LSF 命令补：

```bash
bmonitor_cli job <job_id> --diagnose pend    # 首选：聚合 pending reason + 队列 slot + 资源匹配判断
bjobs -l <job_id>                            # 补充：全量作业信息——资源请求、提交参数、依赖条件细节
bjobs -p <job_id>                            # 补充：Pending 原因详情（bmonitor_cli 缺该字段时用）
```

**必须提取的核心字段：**

- Job ID、提交用户、队列名称、提交节点
- 资源请求：`-R` 资源表达式、`-n` CPU 数、`-q` 指定队列、`-M` 内存限制（如有）
- Pending Reason（原文，不要翻译或猜测）
- 提交时间（判断已排队多久）
- 依赖条件（如有 `-w` 参数）

### Step 2：根据 Pending Reason 深入排查

优先用 `bmonitor_cli` 的等价命令（JSON 输出、可过滤），不足时再用裸 LSF 命令补。按实际 Pending Reason 执行对应检查：

#### 2.1 槽位不足类

当 Pending Reason 包含 "Not enough job slot" 或 "job limit"：

```bash
bmonitor_cli queue <queue_name>     # 首选：真实调度约束（MAX/JL_U/JL_P/RUNLIMIT/SHARES）+ 成员主机
bmonitor_cli users --sort PEND      # 首选：用户级作业计数，定位谁在占 slot
bmonitor_cli hosts --queue <queue_name> --sort-load   # 首选：成员主机状态/slot/负载
# 不足时补充：
bqueues -l <queue_name>             # bmonitor_cli 缺字段时补：NJOBS/MAX/PEND 数量
busers -u <user>                    # 补：用户级别作业数限制（MAX 字段）
```

**诊断逻辑：**
- 比较队列当前 NJOBS vs MAX，如果 NJOBS >= MAX，队列满
- 比较用户 NJOBS vs MAX_JOBS，如果达到上限，用户被限流
- 如果所有节点 NJOBS >= MAX，集群无空闲槽位

#### 2.2 资源不匹配类

当 Pending Reason 包含 "Job requirements not satisfied" 或 "not enough"：

```bash
bmonitor_cli hosts --queue <queue_name> --status unavail --sort-load   # 首选：成员主机状态/slot/负载
# 不足时补充：
lshosts                     # 补：节点硬件配置（maxmem, ncpus, type, model）
lsload -w                   # 补：节点实时负载和可用资源
bhosts -w                   # 补：节点状态和可用槽位
```

**诊断逻辑：**
- 解析 `-R` 资源表达式，确认请求的资源是否合理
- 比较请求资源与集群实际资源（如请求 mem>128G 但最大节点只有 64G）
- 检查是否有节点处于 closed/unavail 状态
- 检查 `-m` 指定的主机/主机组是否有可用节点

#### 2.3 License 不足类

当 Pending Reason 包含 "license" 或 "Not enough"：

```bash
# 首选 bmonitor_cli（经 run_command 调用，输出 JSON）：
bmonitor_cli license --feature <feature>      # 实时 license：issued/in_use/available + 占用者
# 或使用 query_license_info 工具查询 license 使用情况
```

**诊断逻辑：**
- 查看指定 License feature 的 Total/In-use/Free 数量
- 确认是被哪些用户/主机占用

#### 2.4 队列/主机状态异常类

当 Pending Reason 包含 "Queue" 或 "Host"：

```bash
bmonitor_cli queues                       # 首选：所有队列状态总览（Open/Active）
bmonitor_cli queue <queue_name>           # 首选：目标队列详细配置 + 成员主机
bmonitor_cli hosts --queue <queue_name>   # 首选：成员主机状态
# 不足时补充：
bqueues -l <queue_name>                    # bmonitor_cli 缺字段时补
```

**诊断逻辑：**
- 确认队列是否 Open 且 Active
- 确认队列的 HOSTS 列表中是否有可用节点
- 确认队列的时间窗口（RUN_WINDOW）当前是否开放

#### 2.5 依赖条件类

当 Pending Reason 包含 "Dependency" 或 "condition"：

```bash
bjobs -l <job_id>           # 查依赖条件定义
bmonitor_cli job <dep_job_id>    # 查依赖作业当前状态（bjobs 看不到已完成作业时用 db job）
bjobs <dep_job_id>           # 补：依赖作业当前状态（作业仍在 bjobs 中时）
```

**诊断逻辑：**
- 解析 `-w` 依赖表达式
- 确认被依赖的作业是否已完成/失败
- 如果依赖作业已 EXIT，条件可能永远无法满足

#### 2.6 用户限制类

当 Pending Reason 包含 "User" 或 "limit reached"：

```bash
bmonitor_cli users --sort PEND     # 首选：用户当前作业计数（MAX/NJOBS/PEND/RUN）
blimits -u <user>                  # 补：用户资源使用限制（部分集群无此命令，失败则跳过，用 busers -u 的 MAX 字段判断）
busers -u <user>                   # 补：用户作业数限制（blimits 不可用时）
bjobs -u <user> -r                 # 查用户当前正在运行的作业
```

**诊断逻辑：**
- 确认用户是否达到 MAX_JOBS 限制
- 列出用户当前运行的作业，建议哪些可以结束

### Step 3：进阶排查

如果 Step 2 无法确定原因：

1. **Fairshare 排序**：`bmonitor_cli queue <queue>` 或 `bqueues -l <queue>` 查 FAIRSHARE 配置，用户可能因历史使用量被降低优先级
2. **资源预约冲突**：`brsvs` 查是否有资源被预约
3. **调度周期**：如果刚提交不久（< 1 分钟），可能只是调度器还未轮到，等待一个调度周期
4. **作业被挂起**：查 `bjobs -l` 的 STATUS 字段，确认作业是否处于 SUSP/PSUSP/USUSP/SSUSP 状态（被 bstop 或管理员挂起，非真正 PEND 排队）

### Step 4：诊断结论输出规范

```
### PEND 诊断报告

- 作业ID：<job_id>
- 提交用户：<user>
- 所属队列：<queue_name>
- 排队时长：<duration>
- Pending 原因：<原文>

#### 一、根因分析
<基于命令输出的精准根因，引用具体数字（如 NJOBS=100/MAX=100）>

#### 二、立即修复操作
<可直接复制执行的命令和步骤>

#### 三、长期预防建议
<可落地的规避方案>
```
