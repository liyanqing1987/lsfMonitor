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

## 诊断流程

**执行要求：**
1. 所有诊断命令必须通过 run_command 工具直接执行并分析结果，不要将命令以文本形式输出给用户。
2. 如果用户询问的是新的作业ID或距上次查询已有一段时间，必须重新执行命令获取实时数据。
3. 每次诊断必须至少执行 Step 1 的两条命令（bjobs -l 和 bjobs -p），不可跳过。

### Step 0：前置环境检查（严格执行，不要跳过或询问用户）

1. 确认当前用户身份（从 system prompt 中的 Current user 获取）
2. 如果用户没有提供 Job ID，直接运行 `bjobs -p -u <current_user>` 列出所有 PEND 作业
3. 如果只有 1 个 PEND 作业，直接进入 Step 1 诊断
4. 如果有多个 PEND 作业，逐一对每个作业执行 Step 1 诊断，一次性给出所有结果

### Step 1：采集 PEND 作业信息

```bash
bjobs -l <job_id>           # 全量作业信息：资源请求、Pending 原因、提交参数
bjobs -p <job_id>           # Pending 原因详情
```

**必须提取的核心字段：**

- Job ID、提交用户、队列名称、提交节点
- 资源请求：`-R` 资源表达式、`-M` 内存限制、`-n` CPU 数、`-q` 指定队列
- Pending Reason（原文，不要翻译或猜测）
- 提交时间（判断已排队多久）
- 依赖条件（如有 `-w` 参数）

### Step 2：根据 Pending Reason 深入排查

按实际 Pending Reason 执行对应检查命令：

#### 2.1 槽位不足类

当 Pending Reason 包含 "Not enough job slot" 或 "job limit"：

```bash
bqueues -l <queue_name>     # 查 NJOBS/MAX/PEND 数量，确认队列是否已满
busers -u <user>            # 查用户级别的作业数限制
bhosts -w                   # 查各节点当前 NJOBS 和 MAX
```

**诊断逻辑：**
- 比较队列当前 NJOBS vs MAX，如果 NJOBS >= MAX，队列满
- 比较用户 NJOBS vs MAX_JOBS，如果达到上限，用户被限流
- 如果所有节点 NJOBS >= MAX，集群无空闲槽位

#### 2.2 资源不匹配类

当 Pending Reason 包含 "Job requirements not satisfied" 或 "not enough"：

```bash
lshosts                     # 查节点硬件配置（maxmem, ncpus, type, model）
lsload -w                   # 查节点实时负载和可用资源
bhosts -w                   # 查节点状态和可用槽位
```

**诊断逻辑：**
- 解析 `-R` 资源表达式，确认请求的资源是否合理
- 比较请求资源与集群实际资源（如请求 mem>128G 但最大节点只有 64G）
- 检查是否有节点处于 closed/unavail 状态
- 检查 `-m` 指定的主机/主机组是否有可用节点

#### 2.3 License 不足类

当 Pending Reason 包含 "license" 或 "Not enough"：

```bash
# 使用 query_license_info 工具查询 license 使用情况
```

**诊断逻辑：**
- 查看指定 License feature 的 Total/In-use/Free 数量
- 确认是被哪些用户/主机占用

#### 2.4 队列/主机状态异常类

当 Pending Reason 包含 "Queue" 或 "Host"：

```bash
bqueues                     # 查所有队列状态（Open/Active）
bqueues -l <queue_name>     # 查目标队列详细配置
bhosts -w                   # 查节点状态
```

**诊断逻辑：**
- 确认队列是否 Open 且 Active
- 确认队列的 HOSTS 列表中是否有可用节点
- 确认队列的时间窗口（RUN_WINDOW）当前是否开放

#### 2.5 依赖条件类

当 Pending Reason 包含 "Dependency" 或 "condition"：

```bash
bjobs -l <job_id>           # 查依赖条件定义
bjobs <dep_job_id>          # 查依赖作业当前状态
```

**诊断逻辑：**
- 解析 `-w` 依赖表达式
- 确认被依赖的作业是否已完成/失败
- 如果依赖作业已 EXIT，条件可能永远无法满足

#### 2.6 用户限制类

当 Pending Reason 包含 "User" 或 "limit reached"：

```bash
busers -u <user>            # 查用户当前作业数和限制
blimits -u <user>           # 查用户资源使用限制（如果命令可用）
bjobs -u <user> -r          # 查用户当前正在运行的作业
```

**诊断逻辑：**
- 确认用户是否达到 MAX_JOBS 限制
- 列出用户当前运行的作业，建议哪些可以结束

### Step 3：进阶排查

如果 Step 2 无法确定原因：

1. **Fairshare 排序**：`bqueues -l <queue>` 查 FAIRSHARE 配置，用户可能因历史使用量被降低优先级
2. **资源预约冲突**：`brsvs` 查是否有资源被预约
3. **调度周期**：如果刚提交不久（< 1 分钟），可能只是调度器还未轮到，等待一个调度周期
4. **管理员干预**：`bjobs -l` 查是否被 `bstop` 暂停

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
