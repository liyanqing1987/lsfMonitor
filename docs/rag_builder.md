# rag_builder 用户手册

## 简介

`rag_builder` 是 lsfMonitor AI Helpdesk 的 RAG（Retrieval-Augmented Generation）向量数据库构建工具。它从用户提供的文档（PDF、纯文本、Markdown、reStructuredText）中提取文本，生成向量嵌入，并构建 FAISS 索引，供 AI Helpdesk 的文档搜索功能使用。

## 前置条件

1. **已完成 lsfMonitor 安装**（`python3 install.py`），确保 `rag_builder` 的 shell wrapper 已生成。

2. **Embedding API 已配置**。在 `monitor/conf/config.py`（或 `~/.lsfMonitor/conf/config.py`）中至少配置以下其一：

   ```python
   # 方式一：使用独立的 embedding API
   ai_embedding_api_base_url = "https://ark.cn-beijing.volces.com/api/v3"
   ai_embedding_api_key = "your-api-key"
   ai_embedding_model_name = "your-embedding-model-endpoint"

   # 方式二：复用主 AI API（embedding 配置留空时自动回退）
   ai_api_base_url = "https://ark.cn-beijing.volces.com/api/v3"
   ai_api_key = "your-api-key"
   ai_embedding_model_name = "your-embedding-model-endpoint"
   ```

3. **Python 依赖**：
   - `numpy` — 向量计算
   - `faiss-cpu`（或 `faiss-gpu`）— 向量索引
   - `pypdf` — PDF 文本提取（仅处理 PDF 时需要）
   - `requests` — API 调用

   ```bash
   pip install numpy faiss-cpu pypdf requests
   ```

## 用法

```bash
# 构建/追加
./monitor/tools/rag_builder -i <文件或目录> [选项]

# 查看已索引文档
./monitor/tools/rag_builder -l [-o OUTPUT_DIR] [--prefix PREFIX]

# 删除文档
./monitor/tools/rag_builder -d <匹配关键词> [-o OUTPUT_DIR] [--prefix PREFIX]
```

### 参数说明

| 参数 | 缩写 | 默认值 | 说明 |
|------|------|--------|------|
| `--input_files` | `-i` | — | 一个或多个文件/目录路径，目录会递归扫描 |
| `--list` | `-l` | 关闭 | 列出数据库中已索引的所有文档 |
| `--delete` | `-d` | — | 按文件名子串匹配，从数据库中删除指定文档 |
| `--rebuild` | — | 关闭 | 丢弃现有数据，从零重建 |
| `--chunk_size` | — | 700 | 每个文本块的目标字符数 |
| `--chunk_overlap` | — | 100 | 相邻文本块的重叠字符数 |
| `--output_dir` | `-o` | `$LSFMONITOR_INSTALL_PATH/db/ai` | 输出目录 |
| `--prefix` | — | `rag` | 输出文件名前缀（生成 `{prefix}_chunks.json` 等） |
| `--compress` | — | `flat` | FAISS 索引压缩方式（见下方说明） |
| `--batch_size` | — | 10 | 每批 embedding API 调用的 chunk 数 |
| `--workers` | — | 10 | embedding API 并发请求数（加速 embedding 生成） |

> **注意**：`-i`、`-l`、`-d` 三者至少指定其一。

### --compress 索引压缩选项

| 选项 | 类型 | 大小（15K 向量） | 精度 | 说明 |
|------|------|-----------------|------|------|
| `flat` | IndexFlatIP | ~118 MB | 精确 | 默认，无损精确搜索 |
| `sq8` | 8-bit 标量量化 | ~30 MB | 几乎无损 | 每维度 256 级量化 |
| `sq6` | 6-bit 标量量化 | ~22 MB | 很好 | 每维度 64 级量化 |
| `sq4` | 4-bit 标量量化 | ~15 MB | 够用 | 每维度 16 级量化 |
| `pq256` | 乘积量化 (m=256) | ~6 MB | 损失小 | PQ 中精度最好 |
| `pq128` | 乘积量化 (m=128) | ~4 MB | 损失较小 | 精度与体积折中 |
| `pq64` | 乘积量化 (m=64) | ~3 MB | 有损失 | 最小体积 |

PQ 系列需至少 256 条向量用于训练，数据不足时自动回退到 flat。m 越大精度越好、文件越大。

### 支持的文件类型

| 扩展名 | 类型 |
|--------|------|
| `.pdf` | PDF 文档（逐页提取文本） |
| `.txt` | 纯文本 |
| `.md` | Markdown |
| `.rst` | reStructuredText |

## 使用示例

### 首次构建

从一个文档目录构建 RAG 数据库：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild
```

从多个文件和目录混合构建：

```bash
./monitor/tools/rag_builder -i manual.pdf faq.txt /path/to/guides/ --rebuild
```

### 追加新文档

向已有数据库中添加新文档（已索引的文件会自动跳过）：

```bash
./monitor/tools/rag_builder -i /path/to/new_docs/
```

### 查看已索引文档

```bash
./monitor/tools/rag_builder -l
```

输出示例：

```
=== Indexed Documents (4 files, 15049 chunks) ===
  [1] /path/to/spectrum-lsf-10.1.0-documentation.pdf
      14624 chunks, 2477 pages, file exists
  [2] /path/to/volclava 用户手册.pdf
      29 chunks, 17 pages, file exists
```

### 删除文档

按文件名子串匹配删除（删除后自动重建 FAISS 索引）：

```bash
# 删除文件名含 "用户手册" 的文档
./monitor/tools/rag_builder -d "用户手册"

# 同时删除多个
./monitor/tools/rag_builder -d "用户手册" "安装及配置"

# 删除所有 volclava 相关文档
./monitor/tools/rag_builder -d "volclava"
```

删除后可追加新版文档：

```bash
./monitor/tools/rag_builder -d "旧文档" && ./monitor/tools/rag_builder -i /path/to/新文档.pdf
```

### 使用索引压缩

```bash
# 4-bit 标量量化（118MB -> 15MB，精度够用）
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --compress sq4

# 乘积量化（118MB -> 3MB，精度有损失）
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --compress pq64

# 乘积量化，更高精度（118MB -> 4MB）
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --compress pq128
```

### 调整分块参数

对于内容密集的技术文档，可以使用更小的分块：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --chunk_size 500 --chunk_overlap 80
```

### 自定义输出目录和文件前缀

输出到指定目录：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild -o /data/my_rag_db/
```

使用自定义前缀（生成 `lsf_chunks.json`、`lsf_faiss.index` 等）：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --prefix lsf
```

两者组合：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild -o /data/my_rag_db/ --prefix lsf
```

> `-l` 和 `-d` 同样支持 `-o` 和 `--prefix`，用于操作非默认位置/前缀的数据库。

### 加速大文档处理

增大并发数以加速 embedding 生成（默认 10 个 worker）：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --workers 20
```

如果 API 有速率限制，减小并发数和批量大小：

```bash
./monitor/tools/rag_builder -i /path/to/docs/ --rebuild --workers 5 --batch_size 5
```

## 输出文件

工具在输出目录（默认 `$LSFMONITOR_INSTALL_PATH/db/ai/`，可通过 `-o` 指定）下生成以下文件（前缀默认为 `rag`，可通过 `--prefix` 修改）：

| 文件 | 说明 |
|------|------|
| `{prefix}_chunks.json` | 文本块列表（`list[str]`），AI Helpdesk 直接读取此文件 |
| `{prefix}_faiss.index` | FAISS 向量索引，AI Helpdesk 用于语义搜索 |
| `{prefix}_metadata.json` | 元数据数组，记录每个 chunk 的来源文件和页码，用于追加模式去重、`-l` 查看、`-d` 删除和 AI 回答来源标注 |
| `{prefix}_embeddings.npy` | 向量缓存（numpy 数组），追加模式和删除操作时避免对已有 chunk 重新调用 API |

其中 `{prefix}_chunks.json`、`{prefix}_faiss.index` 和 `{prefix}_metadata.json` 是 AI Helpdesk 运行所需的文件（metadata 用于在 AI 回答底部显示来源文件和页码），`{prefix}_embeddings.npy` 仅供 `rag_builder` 自身在追加/删除模式下使用。

> **注意**：bmonitor AI tab 默认读取 `rag_chunks.json` + `rag_faiss.index`，因此使用非默认前缀时需确保 AI Helpdesk 能找到对应文件。

## 工作模式

### 追加模式（默认）

- 读取已有的 `rag_chunks.json`、`rag_metadata.json`、`rag_embeddings.npy`
- 通过 metadata 中的 `source` 字段判断哪些文件已索引，自动跳过
- 仅对新文件提取文本、生成 embedding
- 合并新旧数据后重建 FAISS 索引并保存

### 重建模式（`--rebuild`）

- 忽略所有已有数据，从零开始处理
- 适用于：源文档内容有更新、分块参数变更、首次从旧数据迁移、切换压缩方式

### 查看模式（`-l`/`--list`）

- 读取 metadata 文件，按来源文件聚合统计
- 显示每个文档的 chunk 数、页数、文件是否仍存在

### 删除模式（`-d`/`--delete`）

- 按文件名子串匹配，删除匹配的所有 chunk
- 自动重建 FAISS 索引并保存
- 若删除后数据库为空，则移除所有输出文件

## 注意事项

1. **从旧数据迁移**：如果 `db/ai/` 中已有手动放入的 `rag_chunks.json` 但没有 `rag_embeddings.npy`，追加模式会报错并提示使用 `--rebuild`。这是因为无法在不重新 embedding 的情况下与新数据合并。

2. **API 费用与速率**：每个文本块会调用一次 embedding API（如果 API 支持批量输入则自动合并请求）。大量文档时请注意 API 用量。可用 `--workers` 控制并发数，`--batch_size` 控制每批 chunk 数。

3. **FAISS 索引类型**：默认使用 `IndexFlatIP`（精确内积搜索），可通过 `--compress` 选择压缩索引以减小文件大小。所有索引类型均与 `faiss.read_index()` 兼容，AI Helpdesk 无需修改即可使用。

4. **向量归一化**：embedding 向量在入库前会做 L2 归一化，使内积等价于余弦相似度，与 AI Helpdesk 的查询逻辑一致。

5. **文件编码**：文本文件以 `errors='replace'` 模式读取，可容忍部分编码错误，但建议使用 UTF-8 编码。

6. **PQ 最低数据量**：`--compress pq64/pq128/pq256` 需要至少 256 条向量用于训练。数据量不足时自动回退到 `flat` 模式。

## 故障排查

| 问题 | 解决方法 |
|------|----------|
| `Embedding API not configured` | 检查 config.py 中的 `ai_embedding_*` 或 `ai_api_*` 配置 |
| `pypdf is required for PDF support` | 运行 `pip install pypdf` |
| `Failed to get embedding after 3 attempts` | 检查 API key、网络连接、API 配额 |
| `Please use --rebuild and provide all source files` | 首次迁移旧数据，需要 `--rebuild` 重新生成所有 embedding |
| `No supported files found` | 确认输入路径存在且包含 `.pdf/.txt/.md/.rst` 文件 |
| `PQ requires at least 256 vectors` | 数据量太少无法使用 pq64/pq128/pq256，会自动回退到 flat；或换用 sq4/sq8 |
| `one of -i, -l, -d is required` | 至少指定 `-i`（构建）、`-l`（查看）或 `-d`（删除）之一 |
| `No matching documents found` | `-d` 指定的关键词在已索引文档路径中未匹配，用 `-l` 查看已有文档 |
