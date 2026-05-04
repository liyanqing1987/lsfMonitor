# seedb 用户手册

## 概述

`seedb` 是 lsfMonitor 提供的命令行工具，用于查看 lsfMonitor 的 SQLite 数据库内容。可以列出数据库中的表、查看指定表的数据，并支持按列和行数进行筛选。

## 使用方法

```
seedb -d <数据库文件> [选项]
```

## 命令行参数

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--database` | `-d` | （必需）指定数据库文件路径 |
| `--tables` | `-t` | 指定要查看的表名（可多个，空格分隔） |
| `--keys` | `-k` | 指定要查看的列名（可多个，空格分隔） |
| `--number` | `-n` | 限制输出行数 |

## 数据库路径

- 如果提供的是绝对路径，直接使用该路径。
- 如果提供的是相对路径且文件不存在，工具会自动在 `<db_path>/monitor/` 下查找。

## 使用示例

### 列出数据库中的所有表

```bash
seedb -d /opt/lsfMonitor/db/cluster1/queue.db
```

输出示例：

```
DB_FILE : /opt/lsfMonitor/db/cluster1/queue.db
TABLES  :
========
queue_normal
queue_high
queue_low
========
```

### 查看指定表的全部数据

```bash
seedb -d /opt/lsfMonitor/db/cluster1/queue.db -t queue_normal
```

### 查看指定表的指定列

```bash
seedb -d /opt/lsfMonitor/db/cluster1/queue.db -t queue_normal -k sample_time RUN PEND
```

### 限制输出行数

```bash
seedb -d /opt/lsfMonitor/db/cluster1/queue.db -t queue_normal -n 10
```

### 查看作业数据库

```bash
seedb -d /opt/lsfMonitor/db/cluster1/job/20260429.db -t job -k job_name status queue -n 20
```

## 数据库文件说明

lsfMonitor 使用的数据库文件位于 `<db_path>/<cluster_name>/` 目录下：

| 数据库路径 | 内容 |
|-----------|------|
| `job/<YYYYMMDD>.db` | 按日期存储的已完成作业记录 |
| `job_mem/<range>.db` | 运行中作业的内存采样 |
| `queue.db` | 队列历史（每个队列一张表） |
| `host.db` | 主机状态历史 |
| `load.db` | 主机负载历史 |
| `utilization.db` | 资源利用率历史 |
| `user/<YYYYMMDD>.db` | 按日期存储的用户活动记录 |

## 注意事项

- 该工具为只读工具，不会修改数据库内容。
- 大表查询建议使用 `-n` 限制输出行数，避免终端刷屏。
