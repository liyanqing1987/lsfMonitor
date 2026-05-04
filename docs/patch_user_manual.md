# patch 用户手册

## 概述

`patch` 是 lsfMonitor 提供的升级/补丁工具，用于将新版本安装包的变更同步到已有的安装目录。支持所有文件类型的同步、配置文件自动迁移、Shell 包装脚本重新生成、以及升级前自动备份。

## 使用方法

```
patch -p <新安装包路径> [选项]
```

## 命令行参数

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--patch_path` | `-p` | （必需）指定新安装包的路径 |
| `--dry_run` | `-d` | 预览模式，只显示变更列表，不执行任何修改 |
| `--no-backup` | | 跳过备份步骤 |

## 工作流程

`patch` 工具按以下步骤执行升级：

### 1. 扫描

扫描新安装包和当前安装目录的所有文件，将文件分为四类：

- **新增文件**（+）：仅存在于新安装包中的文件
- **修改文件**（~）：两边都有但内容不同的文件
- **删除文件**（-）：仅存在于当前安装目录中、新版本已移除的文件
- **未变更文件**：两边内容一致，无需处理

### 2. 显示变更摘要

以颜色区分显示所有变更，包括文件级变更和配置迁移信息。

### 3. 用户确认

提示用户确认是否执行变更（`--dry_run` 模式下到此结束）。

### 4. 备份

将所有即将被修改或删除的文件备份到 `<安装目录>/.patch_backup/YYYYMMDD_HHMMSS/` 目录。同时备份当前的 `config.py` 配置文件。可通过 `--no-backup` 跳过此步骤。

### 5. 同步文件

- 复制新增文件（自动创建所需目录）
- 覆盖修改文件
- 删除已移除文件（自动清理空目录）

### 6. 配置迁移

自动合并 `monitor/conf/config.py`：

- 已有变量：保留用户的当前值
- 新版本新增变量：使用新模板的默认值添加
- 用户自定义变量（不在新模板中）：追加保留到文件末尾

### 7. 重新生成 Shell 包装脚本

根据新版本的 `install.py` 中的工具列表，重新生成所有 Shell 包装脚本（如 `monitor/bin/bmonitor`、`monitor/tools/akill` 等），确保路径设置正确。

### 8. 清理

删除安装目录下所有 `__pycache__/` 字节缓存目录，避免旧版本缓存导致的兼容问题。

## 排除规则

以下目录和文件不会被同步处理：

| 排除项 | 原因 |
|--------|------|
| `db/` | 运行时数据库，不可覆盖 |
| `.git/`、`.claude/` | 开发相关文件 |
| `__pycache__/` | 字节缓存，单独清理 |
| `monitor/conf/config.py` | 用户配置，通过迁移机制处理 |
| Shell 包装脚本 | 单独重新生成 |
| `.patch_backup/` | 历史备份 |

## 使用示例

### 预览变更（推荐先执行）

```bash
patch -p /path/to/new/lsfMonitor -d
```

输出示例：

```
Install Path : /opt/lsfMonitor
Patch   Path : /tmp/lsfMonitor_v2.2

=== Patch Summary ===
  New files     : 3
  Modified files: 12
  Deleted files : 1

[New Files]
  + monitor/conf/skills/new_skill/SKILL.md
  + monitor/common/common_new.py
  + data/pictures/new_icon.png

[Modified Files]
  ~ monitor/bin/bmonitor.py
  ~ monitor/common/common_lsf.py
  ...

[Deleted Files]
  - monitor/tools/old_tool.py

[Config Migration]
  Config will be migrated (user values preserved, new variables added).

(Dry run mode - no changes applied.)
```

### 执行升级

```bash
patch -p /path/to/new/lsfMonitor
```

### 升级但不备份

```bash
patch -p /path/to/new/lsfMonitor --no-backup
```

## 回滚

如果升级后发现问题，可以手动从备份恢复：

```bash
# 查看备份
ls <安装目录>/.patch_backup/

# 恢复某次备份（将备份文件复制回安装目录）
cp -r <安装目录>/.patch_backup/20260429_153000/* <安装目录>/
```

## 注意事项

- 首次使用建议先执行 `-d`（dry_run）预览变更，确认无误后再正式执行。
- 升级前确保没有正在运行的 `bmonitor` 或 `bsample` 进程。
- `db/` 目录中的数据库文件不会被影响，升级后历史数据完整保留。
- 如果新版本变更了 Python 依赖，升级后需要手动执行 `pip install -r requirements.txt`。
