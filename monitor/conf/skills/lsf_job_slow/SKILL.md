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

- 作业运行中但无任何输出 / 速度异常缓慢 / 没有进展
- 进程处于 D 状态（不可中断的 IO 等待）
- NFS/网络存储挂载异常导致程序阻塞
- 执行节点负载过高导致争抢资源
- License 等待（程序启动后等 License 才能继续）
- 死锁（多进程/多线程程序）

## 触发条件

用户提问涉及以下内容时自动启用：

- 作业 RUN 状态但卡住了 / 没输出 / 没进展
- 作业跑了很久还没结束 / 比预期慢很多
- 怎么看作业跑到哪了
- 作业是不是挂了 / 死了

---

## 诊断流程

### Step 0：前置环境检查（严格执行，不要跳过或询问用户）

1. 确认当前用户身份（从 system prompt 中的 Current user 获取）
2. 如果用户没有提供 Job ID，直接运行 `bjobs -r -u <current_user>` 列出所有 RUN 作业
3. 找到作业后直接进入 Step 1 诊断，不要反问用户确认

### Step 1：采集运行状态信息

```bash
bjobs -l <job_id>           # 全量信息：执行节点、资源使用、运行时长
bpeek <job_id>              # 实时查看 stdout/stderr 输出（第一手线索）
```

**必须提取的核心字段：**

- 执行节点、运行时长、CPU TIME、MAX MEM
- RUNLIMIT、MEMLIMIT
- 工作目录、执行命令、输出文件路径

**bpeek 关键判断：**
- 有持续输出 → 作业在运行，可能只是慢
- 输出停在某一步 → 定位到具体阻塞点
- 完全无输出 → 可能在初始化，或确实卡死

### Step 2：检查执行节点状态

```bash
lsload <exec_host>          # 节点实时负载：CPU、内存、IO、Swap
bhosts -l <exec_host>       # 节点作业数、状态
```

**异常判断标准：**

| 指标 | 异常判断 |
|---|---|
| r15m (负载) | 远超 CPU 核数 → 节点过载 |
| ut (CPU利用率) | 持续 100% → CPU 争抢 |
| mem (可用内存) | 接近 0 → 内存耗尽，频繁 Swap |
| io (IO等待) | 持续 > 30% → IO 瓶颈 |
| tmp (临时磁盘) | 接近 0 → 磁盘满 |

### Step 3：检查进程状态

```bash
ps -u <user> -o pid,stat,pcpu,pmem,etime,cmd | grep -v grep
```

| 进程状态 | 含义 | 处理方式 |
|---|---|---|
| R | 运行中 | 正常 |
| S | 可中断睡眠 | 正常，等待 IO/事件 |
| D | 不可中断睡眠 | **NFS 挂载卡住或磁盘 IO 故障** |
| Z | 僵尸进程 | 父进程未回收子进程 |
| T | 停止 | 可能被 `bstop` 暂停 |

**D 状态排查：**
```bash
df -h <cwd>                 # 检查文件系统是否正常
ls <cwd> 2>&1               # 如果卡住，确认是 NFS 问题
```

### Step 4：检查应用日志

从 `bjobs -l` 获取 CWD 和 stdout/stderr 路径，检查应用自身的日志输出：

```bash
ls -lt <cwd>/*.log 2>/dev/null | head -10    # 查看 CWD 下最近的日志文件
tail -50 <stderr_file>                       # 查看 stderr 最新内容
tail -50 <stdout_file>                       # 查看 stdout 最新内容
ls -lt <cwd> | head -20                      # 查看 CWD 下最近修改的文件
```

**关注的异常模式：**
- 大量重复的 warning/error 行 → 程序在反复重试某个失败操作
- "timeout"/"connection refused"/"retry" → 等待外部服务/网络资源
- "waiting for license"/"license unavailable" → 应用级 License 等待
- 日志文件长时间未更新（`ls -l` 看 mtime）→ 程序卡在某处
- 日志文件增长极快（`du -h`）→ 可能在刷大量无用输出，IO 瓶颈
- "segfault"/"assertion"/"exception" → 程序虽未退出但已进入异常状态

### Step 5：分场景诊断

#### 5.1 输出停在某步骤
- 是否在等输入（交互式程序在 batch 模式等 stdin）
- 是否在等 License（用 query_license_info 查询）
- 是否死锁（多线程/多进程程序）

#### 5.2 完全无输出
```bash
bjobs -l <job_id>           # 间隔 30 秒查两次，比较 CPU TIME 变化
```
- CPU TIME 增长 → 程序在运行，输出被缓冲
- CPU TIME 不变 → 确实卡住（IO 等待或死锁）
- MEM 持续增长 → 在加载数据

#### 5.3 运行缓慢
```bash
lsload <exec_host>          # 节点是否过载
bjobs -u all -m <exec_host> # 同节点有多少其他作业
```
- 节点过载 → 建议换节点重提交
- Swap 使用高 → 内存不足导致频繁换页
- 应用日志有大量 retry/warning → command 本身有问题

#### 5.4 作业被暂停
```bash
bjobs -l <job_id>           # 查看是否有 SUSPEND 原因
```
- USUSP → 用户 `bstop` 了，`bresume <job_id>` 恢复
- SSUSP → 系统暂停，等待负载下降或联系管理员

### Step 6：进阶排查

1. **strace 跟踪**：`strace -p <pid> -e trace=network,file -f 2>&1 | head -50`
2. **对比运行**：`bsub -I -m <host> <command>` 交互式复现
3. **监控变化**：间隔 30 秒两次 `bjobs -l`，比较 CPU TIME/MEM 是否增长

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
