---
name: lsfmonitor_usage
description: lsfMonitor工具自身的安装、配置、使用、各功能模块说明，覆盖bmonitor/bsample/CLI工具/memPrediction等全部组件
version: 1.0.0
tags:
  - lsfmonitor
  - bmonitor
  - bsample
  - 使用说明
  - seedb
  - akill
  - process_tracer
  - check_issue_reason
  - show_license_feature_usage
  - rag_builder
---

# lsfMonitor 工具使用百科

## 一、项目简介

lsfMonitor 是一款面向 IBM LSF / OpenLava / Volclava HPC 集群的监控工具。核心功能：
- **数据采样**（bsample）：定期采集作业、队列、主机、负载、利用率、License 等指标，存入 SQLite
- **可视化监控**（bmonitor）：PyQt5 桌面 GUI，多 Tab 浏览集群全貌
- **CLI 工具集**：批量杀作业(akill)、查库(seedb)、诊断(check_issue_reason)、进程追踪(process_tracer)等
- **AI 助手**：内置 LLM 对话，支持执行命令、查 License、查作业历史、搜索文档
- **内存预测**（memPrediction）：基于 XGBoost 的作业内存用量预测子系统

---

## 二、安装与部署

### 2.1 安装

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 执行安装脚本
python3 install.py [-p PREFIX] [-f] [-m]
```

| 参数 | 说明 |
|------|------|
| `-p PREFIX` | 安装路径（默认当前目录） |
| `-f` | 强制安装，覆盖已有 config.py |
| `-m` | 同时安装 memPrediction 子系统 |
| `-c` | 清理旧安装（删除 shell 脚本和配置） |

安装脚本会：
1. 检查 Python 版本（要求 3.12.12+）
2. 在 `monitor/bin/` 和 `monitor/tools/` 下生成 shell 包装脚本（设置 `LSFMONITOR_INSTALL_PATH` 等环境变量）
3. 生成默认配置文件 `monitor/conf/config.py`
4. 创建 `db/` 数据目录

### 2.2 升级/打补丁

```bash
# 用 patch 工具从新包同步到已有安装
monitor/tools/patch -p /path/to/new/package [-d] [--no-backup]
```

| 参数 | 说明 |
|------|------|
| `-p` | 新版本包路径（必需） |
| `-d` | 预览模式，不实际修改 |
| `--no-backup` | 跳过备份 |

---

## 三、配置文件（config.py）

配置文件路径：`{INSTALL_PATH}/monitor/conf/config.py`
用户可在 `~/.lsfMonitor/conf/config.py` 放置个人覆盖配置（优先级更高）。

### 核心配置项

| 变量 | 说明 | 示例 |
|------|------|------|
| `db_path` | SQLite 数据库存储根目录 | `/data/lsfMonitor/db` |
| `license_administrators` | License 管理员过滤（`"all"` 表示所有人可见） | `"all"` |
| `lmstat_path` | lmstat 二进制路径 | `/path/to/lmstat` |
| `lmstat_bsub_command` | 通过 bsub 执行 lmstat 的命令前缀 | `"bsub -q normal -Is"` |
| `excluded_license_servers` | 排除的 License 服务器（格式 `"port@host"` 空格分隔） | `""` |

### AI 相关配置

| 变量 | 说明 |
|------|------|
| `ai_api_base_url` | LLM API 地址（OpenAI 兼容格式） |
| `ai_api_key` | API 密钥 |
| `ai_model_name` | 模型名称或 endpoint ID |
| `ai_embedding_api_base_url` | Embedding 模型 API 地址（为空则回退到 `ai_api_base_url`） |
| `ai_embedding_api_key` | Embedding 模型 API 密钥（为空则回退到 `ai_api_key`） |
| `ai_embedding_model_name` | RAG 向量检索用的 embedding 模型 |
| `ai_dangerous_commands` | 需用户确认的危险命令（空格分隔，默认 `bkill badmin brestart bstop bresume bswitch rm kill killall shutdown reboot mkfs dd`） |

> **三项 AI 必填**：`ai_api_base_url`、`ai_api_key`、`ai_model_name` 均非空时 AI 标签页才可用。

---

## 四、数据采样（bsample）

bsample 负责从集群采集数据写入 SQLite，通常由 crontab 定时执行。

### 4.1 命令格式

```bash
monitor/bin/bsample [选项]
```

### 4.2 采样选项

| 参数 | 说明 | 对应 LSF 命令 |
|------|------|--------------|
| `-j` | 采集已完成作业信息 | `bjobs -u all -d -UF` |
| `-m` | 采集运行中作业内存和idle_factor | `bjobs -u all -r -UF` |
| `-q` | 采集队列信息 | `bqueues` |
| `-qH` | 采集队列-主机映射关系 | `bqueues -l` |
| `-H` | 采集主机状态 | `bhosts` |
| `-l` | 采集主机负载（ut/tmp/swp/mem） | `lsload` |
| `-u` | 采集用户作业统计 | `bjobs -u all -d -UF` |
| `-U` | 采集利用率（slot/cpu/mem） | `lsload/bhosts/lshosts` |
| `-UD` | 计算并保存日利用率 | 基于 -U 数据统计 |
| `-c` | 按条目上限清理数据库 | — |

### 4.3 推荐 crontab 配置

```crontab
3 0 * * * /path/to/monitor/bin/bsample -c         # 清理旧数据
10 11,23 * * * /path/to/monitor/bin/bsample -j     # 作业历史（一天两次）
*/5 * * * * /path/to/monitor/bin/bsample -m        # 作业内存和idle_factor
*/5 * * * * /path/to/monitor/bin/bsample -q        # 队列
*/10 * * * * /path/to/monitor/bin/bsample -qH      # 队列-主机映射
*/5 * * * * /path/to/monitor/bin/bsample -H        # 主机
*/5 * * * * /path/to/monitor/bin/bsample -l        # 负载
30 11,23 * * * /path/to/monitor/bin/bsample -u     # 用户（一天两次）
*/10 * * * * /path/to/monitor/bin/bsample -U       # 利用率
55 23 * * * /path/to/monitor/bin/bsample -UD       # 日利用率
```

> 注意：crontab 中需设置 PATH 和 LSF_* 环境变量，否则 bjobs 等命令无法执行。

### 4.4 数据库结构

数据存储在 `{db_path}/{cluster_name}/` 下：

| 路径 | 说明 | 生成命令 |
|------|------|----------|
| `host.db` | 主机静态信息 | `bsample -H` |
| `job/{date}` | 作业历史（json 格式） | `bsample -j` |
| `job_data/*.db` | 作业内存和idle_factor | `bsample -m` |
| `job_mem/*.db` | 旧版作业内存用量（已废弃，保留兼容读取） | — |
| `load.db` | 主机负载信息 | `bsample -l` |
| `queue.db` | 队列 run/pend slot 信息 | `bsample -q` |
| `queue_host_mapping.db` | 队列-主机映射 | `bsample -qH` |
| `user/{date}` | 用户作业统计 | `bsample -u` |
| `utilization.db` | slot/cpu/mem 利用率 | `bsample -U` |
| `utilization_day.db` | 按天汇聚的利用率 | `bsample -UD` |

AI 相关数据存储在 `{db_path}/ai/` 下（不按 cluster 分）。

---

## 五、GUI 监控（bmonitor）

### 5.1 启动

```bash
monitor/bin/bmonitor [-j JOBID] [-u USER] [-f FEATURE] [-t TAB] [-d] [--disable_license]
```

| 参数 | 说明 |
|------|------|
| `-j JOBID` | 启动后直接在 JOB 标签页显示指定作业 |
| `-u USER` | 在 JOBS 标签页过滤指定用户的作业 |
| `-f FEATURE` | 在 LICENSE 标签页定位指定 License feature |
| `-t TAB` | 启动时切换到指定标签页（JOB/JOBS/HOSTS/LOAD/USERS/QUEUES/UTILIZATION/LICENSE/AI） |
| `-d` | 启用暗黑模式 |
| `--disable_license` | 禁用 License 检查（加速启动） |

### 5.2 标签页说明

| 标签页 | 功能 |
|--------|------|
| **JOB** | 单个作业详情查看（bjobs -UF 格式） |
| **JOBS** | 作业列表浏览，支持按用户/状态/队列筛选 |
| **HOSTS** | 集群主机状态总览（bhosts 数据） |
| **LOAD** | 主机负载曲线（ut/tmp/swp/mem 历史趋势） |
| **USERS** | 用户作业统计（懒加载） |
| **QUEUES** | 队列状态与详情 |
| **UTILIZATION** | 集群利用率统计与趋势（slot/cpu/mem，懒加载） |
| **LICENSE** | EDA License 使用情况与过期时间（懒加载） |
| **AI** | AI 智能助手对话界面 |

> USERS、UTILIZATION、LICENSE 标签页采用懒加载，首次切换时才查询数据。

### 5.3 菜单栏

| 菜单 | 功能 |
|------|------|
| **File** | 导出各标签页表格数据、退出 |
| **Setup** | 启用队列/利用率详情模式 |
| **Function** | Check Pend/Slow/Fail reason（调用 check_issue_reason 工具） |
| **AI** | 记录检索(Record Search)、问题分析(Problem Analysis)、记录清理(Record Cleanup) |
| **Help** | 版本信息、关于 |

---

## 六、CLI 工具集

### 6.1 akill — 批量杀作业

```bash
monitor/tools/akill [-j JOBID] [-J NAME] [-c CMD] [-q QUEUE] [-u USER] [-m HOST] [-s TIME]
```

支持模糊匹配和范围指定（如 `-j 10200-10450`），多条件组合过滤后批量 bkill。

### 6.2 seedb — 查看数据库

```bash
monitor/tools/seedb -d DATABASE [-t TABLES] [-k KEYS] [-n NUMBER]
```

查看 lsfMonitor 的 SQLite 数据库内容，支持指定表、列、行数。

### 6.3 check_issue_reason — 作业异常诊断

```bash
monitor/tools/check_issue_reason -j JOBID [-i ISSUE]
```

PyQt5 GUI，诊断作业 PEND/SLOW/FAIL 的原因，参考 exit_code.yaml 和 term_signal.yaml。

### 6.4 process_tracer — 进程追踪

```bash
monitor/tools/process_tracer [-j JOBID | -p PID]
```

PyQt5 GUI，显示 LSF 作业或本地进程的进程树。

### 6.5 show_license_feature_usage — License 用量详情

```bash
monitor/tools/show_license_feature_usage -s SERVER -v VENDOR -f FEATURE
```

PyQt5 GUI，展示指定 License feature 的详细使用信息。

### 6.6 patch — 升级打补丁

```bash
monitor/tools/patch -p PATCH_PATH [-d] [--no-backup]
```

从新版包同步文件到已有安装，自动处理配置迁移。

### 6.7 rag_builder — RAG 向量数据库构建

```bash
monitor/tools/rag_builder -i FILE [FILE ...] [-o OUTPUT_DIR] [--prefix PREFIX] [--rebuild]
                           [--compress {flat,sq8,sq6,sq4,pq256,pq128,pq64}]
                           [-l] [-d DELETE ...] [--chunk_size N] [--chunk_overlap N]
                           [--batch_size N] [--workers N]
```

构建和管理 AI 助手使用的 RAG 向量数据库，支持 PDF/txt/md/rst 文档输入，默认追加模式。

---

## 七、AI 助手

### 7.1 功能

AI 标签页内置 LLM 对话助手，具备以下能力（通过 tool-use 实现）：

| 工具 | 功能 |
|------|------|
| `run_command` | 在集群上执行 LSF/Linux 命令（有禁止/危险命令分级） |
| `query_license_info` | 查询 EDA License 使用情况（按 feature/user/server） |
| `query_job_history` | 查询历史作业记录（按 job_id/user/queue/status/date） |
| `search_documentation` | 基于 FAISS 的 RAG 文档检索 |

### 7.2 对话日志

所有对话自动保存到 `{db_path}/ai/ai_log.db`，按用户分表（`conversations_{user}`）。
通过菜单 AI → Record Search 可检索历史对话，AI → Problem Analysis 可生成分类分析报告。

### 7.3 经验固化

标记为 `solved` 的历史对话会自动注入后续相似问题的 system prompt，帮助 AI 参考过往成功案例。

---

## 八、memPrediction 内存预测子系统

安装时加 `-m` 参数启用。提供：

| 组件 | 说明 |
|------|------|
| `sample.py` | 采集训练数据 |
| `train.py` | XGBoost 模型训练 |
| `predict.py` | 作业内存用量预测 |
| `report.py` | 预测报告生成 |
| `web_app/` | Flask 后端 + React 前端 Web 界面 |

---

## 九、常见问题

### Q: bmonitor 启动很慢？
- 加 `--disable_license` 跳过 License 检查
- USERS/UTILIZATION/LICENSE 标签页是懒加载的，不会拖慢启动

### Q: bsample 采集不到数据？
- 确认 LSF 环境已加载（`lsid` 能正常返回）
- 确认 `LSFMONITOR_INSTALL_PATH` 环境变量已设置（用 shell 包装脚本启动即可）
- 检查 `db_path` 目录权限

### Q: 如何查看历史作业？
- bmonitor JOBS 标签页浏览近期作业
- `seedb -d job/{date}.db` 直接查 SQLite
- AI 助手中使用 `query_job_history` 工具

### Q: 数据库文件太大？
- `bsample -c` 按条目上限清理（job_data/ 保留30天数据）
- AI 对话记录：菜单 AI → Record Cleanup

### Q: AI 助手回答不准确？
- 确认 `ai_api_base_url`、`ai_api_key`、`ai_model_name` 配置正确
- 将有用的对话标记为 solved，后续相似问题会自动参考
- 可在 `monitor/conf/skills/` 下添加自定义 skill 扩展 AI 知识

### Q: 如何添加自定义 AI skill？
在 `{INSTALL_PATH}/monitor/conf/skills/` 下创建子目录，放入 `SKILL.md` 文件：
```
---
tags:
  - 关键词1
  - 关键词2
---
# Skill 正文（会注入到 system prompt）
```
用户提问包含任意 tag 时，skill 内容会自动注入。

### Q: 个人配置如何覆盖全局配置？
将修改后的 config.py 放到 `~/.lsfMonitor/conf/config.py`，该文件优先于安装目录下的全局配置。
