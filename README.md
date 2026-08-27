# file-brief

[![npm version](https://img.shields.io/npm/v/file-brief)](https://www.npmjs.com/package/file-brief)
[![npm downloads](https://img.shields.io/npm/dm/file-brief)](https://www.npmjs.com/package/file-brief)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Zhiyi-Zhao/file-brief/actions/workflows/validate.yml/badge.svg)](https://github.com/Zhiyi-Zhao/file-brief/actions/workflows/validate.yml)

[中文](#中文) · [English](#english)

`file-brief` is an agent-agnostic skill (OpenAI Codex · Claude Code · DeepSeek Harness) that turns repeated input-file inspection into reusable, task-local documentation.

---

## 中文

### 为什么发布这个仓库

当 Agent 开始数据分析、代码生成、文件转换或任何涉及本地文件的工程任务时，往往会先重复执行同一批检查：

- 文件是什么格式？
- 表格有多少列，各列是什么类型？
- JSON、YAML 或嵌套对象如何组织？
- Excel 有哪些工作表？SQLite 有哪些表和视图？
- 压缩包里有什么？XML/HTML 有哪些标签结构？
- 哪些字段存在缺失？文件是否已经变化？

这些检查本身很有必要，但如果每个会话、每个 Agent 都重新执行，会产生三个问题：

1. **浪费时间和上下文**：相同输入文件被反复读取，真正用于解决任务的上下文反而减少。
2. **污染正式代码**：临时的 `head()`、`str()`、`read_csv()`、字段打印和调试逻辑容易留在最终脚本中。
3. **难以复用知识**：即使同一文件在另一个任务或会话中再次使用，之前发现的数据结构通常没有被保存。

这个仓库发布 `file-brief` 技能，把上述检查集中为一个可复用的预检层。技能为每个大型任务维护独立的 `.file-catalog`，生成简洁的 Markdown 说明和 SQLite 检索索引。后续 Agent 可以先读取说明，只在文件新增或变化时重新解析。

技能本身与具体 Agent 平台无关：任何能执行 Python 命令的 Agent（OpenAI Codex、Claude Code、DeepSeek Harness 等）都可以按同一工作流使用。

### 支持的平台与安装

| 平台 | 安装位置 | 说明 |
|---|---|---|
| OpenAI Codex | `<CODEX_HOME 或 ~/.codex>/skills/file-brief` | 原生 skill 机制 |
| Claude Code | `~/.claude/skills/file-brief` | 原生 Agent Skills 机制 |
| DeepSeek Harness | `~/.dsh/skills/` 或 `~/.agents/skills/`（也可用项目内 `.dsh/skills`、`.agents/skills`） | 自动发现 `SKILL.md` |

一键安装（在仓库根目录）：

```powershell
# Windows PowerShell：安装到全部平台
powershell -ExecutionPolicy Bypass -File .\install.ps1
# 或指定平台：.\install.ps1 -Target codex,claude,dsh,agents
```

```bash
# macOS/Linux：安装到全部平台
./install.sh
# 或指定平台：./install.sh codex claude dsh
```

也可以手动复制：

```powershell
# Windows：DeepSeek Harness
Copy-Item -LiteralPath ".\skills\file-brief" -Destination "$HOME\.dsh\skills\file-brief" -Recurse
```

```bash
# macOS/Linux：Claude Code
mkdir -p ~/.claude/skills && cp -R skills/file-brief ~/.claude/skills/
```

**npm 安装（DeepSeek Harness 插件方式）**：`file-brief@2.0.0` 已发布到 npm（声明 `dsh.bundle`），可以作为 DSH 插件安装，也可在 [deepseek1024.com](https://deepseek1024.com/) 插件商店详情页获得一键安装命令：

```bash
dsh plugin --profile web add file-brief
# 或
npx @deepseek-ai/dsh plugin --profile web add file-brief
```

> 说明：作为 npm 插件安装时，bundle 的 patch 层是空操作（技能本体通过 SKILL.md 加载）；仍建议把技能复制到 `~/.dsh/skills` 等技能根目录以获得最佳体验。

安装后请启动一个新的 Agent 会话，使技能列表重新加载。

### 为什么每个任务拥有自己的目录

解释文档保存在 `<task-root>/.file-catalog`，而不是任何 Agent 的全局目录，原因是：

- 数据说明与对应任务一起移动、备份和归档。
- 不同任务不会互相污染索引。
- 任务内相对路径保持稳定，整个项目目录移动后仍能匹配。
- Markdown 说明可以选择性纳入版本控制。
- SQLite、锁文件和临时文件默认被 `.file-catalog/.gitignore` 排除。

### 隐私设计

技能的目标是保存“结构知识”，不是复制数据。生成的说明允许包含：

- 文件名、格式、大小、修改时间和 SHA-256；
- 字段名、键名、工作表名、表名、视图名、函数名、类名、压缩包成员名和对象名；
- 类型、维度、缺失量、样本内近似唯一值数量；
- PDF 页数、图片尺寸、文档结构计数、XML/HTML 标签计数、归档成员统计。

说明不会保存：

- 原始数据行、单元格样例或数据库单元格值；
- 正文段落、源代码片段或 notebook 单元格内容；
- 类别的实际值或高频值；
- 图片像素、PDF 正文、压缩包成员内容。

解析器可能在内存中读取有界样本以推断结构，但不会把样本值写入目录。

### 一个面向未来的问题

> 未来是否会出现一种专供 AI 阅读的新文件格式，将文件内容说明、数据结构、字段语义、来源和更新状态直接整合到文件本身，从而让这些信息能够随文件在不同 Agent、工具和平台之间传播，而无需每次重新解析？

### 支持的文件

| 类别 | 格式 | 说明 |
|---|---|---|
| 分隔表格 | CSV、TSV、TAB 及分号/竖线分隔变体 | 自动探测分隔符；采样字段类型、缺失率和近似唯一值数量 |
| 工作簿 | XLSX、XLS、XLSM、ODS | 工作表、字段和每表有界样本结构 |
| 列式数据 | Parquet、Feather、Arrow IPC | 使用元数据读取字段、行组和记录批次 |
| 数据库 | SQLite（.db/.sqlite/.sqlite3） | 只读方式列出表、视图、索引、列声明类型和行数 |
| 结构化文本 | JSON、JSONL、NDJSON、YAML、TOML | 键、嵌套层级和元素类型 |
| 归档 | ZIP/JAR/WAR/APK、TAR/TGZ/TBZ2/TXZ、GZIP | 成员清单、类型直方图和压缩统计，不读取成员内容 |
| 标记文档 | XML/XSD/SVG/KML/GPX、HTML | 标签计数、属性键、命名空间、标题/表格/链接结构 |
| Notebook | Jupyter（.ipynb） | 单元格类型分布、执行状态、内核语言，不保存单元格内容 |
| 统计软件 | Stata（.dta） | 变量名、类型与有界样本结构 |
| R 数据 | RDS、RDA、RData | 对象、类、维度、列、列表成员和缺失量 |
| 代码与文本 | Python、R、JS/TS、Java、Go、Rust、C/C++、C#、Ruby、PHP、Kotlin、Swift、Scala、Julia、Lua、Perl、Dart、Elixir、Haskell、Erlang、F#、VB、Shell、PowerShell、Markdown 及常见文本 | 编码、行数、声明、导入和标题结构 |
| 文档与媒体 | PDF、DOCX、常见图片 | 页面、段落、表格、尺寸和元数据键 |
| 其他 | 未知文本或二进制 | 至少生成 MIME 类型和基础元数据说明 |

解析库缺失或文件无法深度读取时，技能会生成带有 `unsupported` 或 `error` 状态的通用说明，而不是静默失败。

### 环境要求

- Python 3.9 或更高版本。
- 核心索引、SQLite/归档/XML/HTML/notebook 解析和大多数源代码分析只依赖标准库。
- 完整格式支持建议安装：

```bash
python -m pip install pandas openpyxl pyarrow PyYAML pypdf Pillow tomli
```

- RDS/RData 深度解析需要：
  - `Rscript`；
  - R 包 `jsonlite`。

Rscript 的发现顺序是：

1. 环境变量 `R_SCRIPT_EXE`；
2. 系统 `PATH` 中的 `Rscript`；
3. Windows 常见 R 安装目录。

### 使用（与语言和平台无关）

以下示例中的 `<skill-dir>` 是已安装的 `file-brief` 技能目录。任何任务——数据分析、Web 项目、配置管线、迁移脚本——都遵循同一工作流。

#### 选择任务根目录

`--task-root` 应指向包含当前大型任务全部输入、脚本和输出的最高合理目录。

- 优先使用用户明确指定的任务目录。
- 未指定时使用当前工作目录。
- 不自动向上搜索 Git 根目录。
- 所有待建档文件必须位于任务根目录内。

#### 首次建档

```bash
python "<skill-dir>/scripts/file_catalog.py" catalog --task-root "/work/my-task"
```

不提供具体文件时会递归处理整个任务目录，并跳过 `.git`、`.file-catalog`、依赖目录、虚拟环境和缓存目录。

#### 开始任务前查询

```bash
python "<skill-dir>/scripts/file_catalog.py" lookup \
  --task-root "/work/my-task" \
  "data/observations.csv"
```

如果返回 `fresh`，Agent 应优先读取返回的 Markdown 文档，而不是再次探查源文件。需要机器可读输出时追加 `--json`。

#### 刷新增或变化的文件

```bash
python "<skill-dir>/scripts/file_catalog.py" catalog \
  --task-root "/work/my-task" \
  "data/observations.csv"
```

技能先比较文件大小和高精度修改时间；只有缺失或变化（或解析器版本升级）的条目才重新计算 SHA-256 并解析。

#### 跨子目录搜索与目录概览

```bash
python "<skill-dir>/scripts/file_catalog.py" search \
  --task-root "/work/my-task" \
  "species"

python "<skill-dir>/scripts/file_catalog.py" info \
  --task-root "/work/my-task"
```

搜索范围包括相对路径、文件名、格式、摘要、字段名、键名和其他结构标识符。`info` 输出按状态和格式的条目统计。跳过不需要的目录或文件：`--exclude "cache,tmp.sqlite"`。

### `.file-catalog` 的结构

```text
<task-root>/
└── .file-catalog/
    ├── INDEX.md
    ├── documents/
    │   └── <relative-path-hash>.md
    ├── catalog.sqlite3
    └── .gitignore
```

- `INDEX.md`：适合人工浏览的紧凑索引。
- `documents/`：每个任务相对路径对应一份当前说明。
- `catalog.sqlite3`：供 `lookup` 和 `search` 使用的机器索引。
- `.gitignore`：只排除 SQLite、锁和临时文件，Markdown 可以提交。

### 状态含义

| 状态 | 含义 | 推荐动作 |
|---|---|---|
| `fresh` | 说明存在且文件大小/修改时间一致 | 直接读取说明 |
| `stale` | 源文件自上次解析后发生变化 | 运行 `catalog` 刷新 |
| `missing` | 文件或说明不存在 | 检查路径；存在源文件时运行 `catalog` |
| `unsupported` | 没有深度解析器，但已有通用元数据 | 使用现有说明，必要时人工检查 |
| `error` | 深度解析失败，并已生成降级说明 | 阅读警告，修复依赖或文件问题后刷新 |

### 示例结果

SQLite 说明只保留表结构，不读取单元格值：

```json
{
  "table_count": 2,
  "tables": [
    {"name": "measurements", "column_count": 3, "row_count": 1250,
     "columns": [
       {"name": "species", "declared_type": "TEXT", "notnull": true}
     ]}
  ]
}
```

ZIP 归档说明列出成员与类型直方图，不读取成员内容：

```json
{
  "member_count": 42,
  "member_names": ["data/a.csv", "data/b.csv", "README.md"],
  "extension_histogram": [{"extension": ".csv", "count": 2}]
}
```

CSV 说明自动记录探测到的分隔符：

```json
{
  "delimiter": ";",
  "column_count": 3,
  "columns": [{"name": "species", "dtype": "object", "missing_percent_in_sample": 1.2}]
}
```

这些示例是结构示意，不包含真实输入数据。

### 推荐工作流

```text
确定 task root
  → lookup 输入文件
  → fresh：读取说明
  → missing/stale：catalog 后读取说明
  → 只有说明不足时才检查原文件
  → 将实际业务逻辑写入正式代码
```

不要把一次性的字段打印、样本输出和格式探测重新写入生产脚本。

### 本版本改进与下一步方向

v2 已实现：

- **平台无关化**：同一 `SKILL.md` 同时适配 OpenAI Codex、Claude Code 与 DeepSeek Harness；提供 `install.ps1` / `install.sh` 一键安装。
- **格式覆盖扩展**：SQLite、ZIP/JAR/APK、TAR/TGZ、GZIP、XML、HTML、Jupyter notebook、Stata，全部仅依赖标准库（Stata 除外）。
- **通用化**：CSV/TSV 自动探测分隔符；20+ 编程语言的表驱动结构提取；工作流与语言/技术栈无关。
- **工程改进**：`--json` 机器可读输出、`info` 子命令、`--exclude` 排除项、并行解析（有界线程池）、解析器版本化（升级后自动重新解析旧条目）、搜索通配符转义。

未来方向（欢迎贡献）：

- 数据格式侧：HDF5/NetCDF、SAS/SPSS、地理空间（GeoJSON/Shapefile）深度解析。
- 结构侧：目录级聚合说明（一个文档描述整个子目录树）；跨任务全局索引。
- 语义侧：可选 LLM 摘要层，用模型生成字段语义（仍不写入原始值）。
- 格式侧：尝试为“AI 原生文件格式”提供预检支持。

### 常见问题

**为什么没有精确统计 CSV 总行数？**  
默认使用有界样本，以避免为结构说明完整扫描超大文本表格。文档会明确标注采样范围。

**为什么某个 Excel 文件显示 `error`？**  
旧式 XLS 或特殊工作簿可能需要额外解析库。安装对应 pandas 引擎后重新运行 `catalog`。

**为什么 TOML 降级？**  
Python 3.11+ 内置 `tomllib`；Python 3.9/3.10 需要安装 `tomli`。

**为什么 R 数据无法解析？**  
确认 `Rscript` 可用，并运行 `Rscript -e "install.packages('jsonlite')"`。也可以设置 `R_SCRIPT_EXE` 为可执行文件路径。

**SQLite 说明会读取我的数据吗？**  
不会。SQLite 解析以只读 URI 打开数据库并启用 `query_only`，只读取 `sqlite_master` 的 schema 信息和行数，不读取任何单元格值。

**可以提交 `.file-catalog` 吗？**  
可以提交 `INDEX.md` 和 `documents/`。SQLite 和临时文件默认被目录内 `.gitignore` 排除。

**会不会把隐私数据写入说明？**  
设计上不会保存原始行、单元格、正文、归档成员内容或类别值。对于高度敏感的数据，仍建议在提交生成的 Markdown 前进行组织自己的安全审查。

---

## English

### Why this repository exists

Before an agent can analyze data, generate code, transform files, or work on any local-file engineering task, it usually repeats the same discovery work:

- What format is this file?
- Which columns exist, and what are their types?
- How are JSON, YAML, or nested objects organized?
- Which worksheets, tables, and views are present?
- What is inside an archive? Which tags does an XML/HTML document use?
- Where are values missing?
- Has the file changed since the previous task?

Those checks are necessary, but repeating them in every session creates three problems:

1. **Time and context are wasted.** The same inputs are reopened while less context remains for the actual task.
2. **Production code becomes noisy.** Temporary `head()`, `str()`, `read_csv()`, schema prints, and debugging logic leak into final scripts.
3. **Knowledge is not reusable.** A later agent or session usually has to rediscover the same structure.

This repository publishes the `file-brief` skill as a reusable preflight layer. Each large task receives its own `.file-catalog` with concise Markdown explanations and a SQLite search index. Later agents can reuse those explanations and reparse only files that are new or stale.

The skill is agent-platform agnostic: any agent that can execute Python commands — OpenAI Codex, Claude Code, DeepSeek Harness, and others — uses the same workflow.

### Supported platforms and installation

| Platform | Install location | Notes |
|---|---|---|
| OpenAI Codex | `<CODEX_HOME or ~/.codex>/skills/file-brief` | Native skill mechanism |
| Claude Code | `~/.claude/skills/file-brief` | Native Agent Skills mechanism |
| DeepSeek Harness | `~/.dsh/skills/` or `~/.agents/skills/` (project `.dsh/skills` and `.agents/skills` also work) | Auto-discovers `SKILL.md` |

One-shot install (from the repository root):

```powershell
# Windows PowerShell: install to every platform
powershell -ExecutionPolicy Bypass -File .\install.ps1
# or select targets: .\install.ps1 -Target codex,claude,dsh,agents
```

```bash
# macOS/Linux: install to every platform
./install.sh
# or select targets: ./install.sh codex claude dsh
```

Manual copy also works:

```bash
# macOS/Linux: Claude Code
mkdir -p ~/.claude/skills && cp -R skills/file-brief ~/.claude/skills/
```

**Install from npm (DeepSeek Harness plugin style)**: `file-brief@2.0.0` is published to npm with a `dsh.bundle` declaration, so it can be installed as a DSH plugin, and the [deepseek1024.com](https://deepseek1024.com/) store shows a one-click install command on its plugin page:

```bash
dsh plugin --profile web add file-brief
# or
npx @deepseek-ai/dsh plugin --profile web add file-brief
```

> Note: installed as an npm plugin, the bundle's patch layer is a no-op (the skill itself loads through SKILL.md); copying the skill into `~/.dsh/skills` or another skill root still gives the best experience.

Start a new agent session after installation so the skill list reloads.

### Why catalogs are task-local

Generated documentation lives under `<task-root>/.file-catalog`, not in any agent-global directory:

- Explanations move, archive, and back up with the task.
- Independent tasks cannot contaminate one another's indexes.
- Task-relative paths remain stable when the whole directory moves.
- Markdown explanations can be version-controlled when useful.
- SQLite, locks, and temporary files are ignored by the generated `.gitignore`.

### Privacy model

The skill stores structural knowledge, not a copy of the data. Explanations may contain:

- file names, formats, sizes, timestamps, and SHA-256 hashes;
- column, key, worksheet, table, view, function, class, archive-member, and object names;
- types, dimensions, missing counts, and approximate distinct counts within a sample;
- PDF page counts, image dimensions, document structure counts, XML/HTML tag counts, and archive member statistics.

They intentionally omit:

- raw rows, cell samples, and database cell values;
- paragraph excerpts, source-code snippets, and notebook cell contents;
- actual category values and frequency lists;
- image pixels, PDF text, and archive member contents.

Parsers may inspect a bounded in-memory sample to infer structure, but sample values are not written to the catalog.

### A question for the future

> Will a new file format emerge specifically for AI consumption—one that embeds content descriptions, data structures, field semantics, provenance, and update status directly into the file itself, allowing this knowledge to travel with the file across agents, tools, and platforms without being re-derived each time?

### Supported files

| Category | Formats | Structural information |
|---|---|---|
| Delimited tables | CSV, TSV, TAB and semicolon/pipe variants | Automatic delimiter detection; sampled types, missing rates, and approximate distinct counts |
| Workbooks | XLSX, XLS, XLSM, ODS | Worksheets, fields, and bounded per-sheet samples |
| Columnar data | Parquet, Feather, Arrow IPC | Schema, row groups, and record batches from metadata |
| Databases | SQLite (.db/.sqlite/.sqlite3) | Read-only tables, views, indexes, declared column types, and row counts |
| Structured text | JSON, JSONL, NDJSON, YAML, TOML | Keys, nesting, and element types |
| Archives | ZIP/JAR/WAR/APK, TAR/TGZ/TBZ2/TXZ, GZIP | Member lists, type histograms, and compression stats; contents never read |
| Markup documents | XML/XSD/SVG/KML/GPX, HTML | Tag counts, attribute keys, namespaces, heading/table/link structure |
| Notebooks | Jupyter (.ipynb) | Cell-type distribution, execution state, kernel language; cell contents never stored |
| Statistical software | Stata (.dta) | Variable names, types, and bounded sample structure |
| R data | RDS, RDA, RData | Objects, classes, dimensions, columns, members, and missing counts |
| Code and text | Python, R, JS/TS, Java, Go, Rust, C/C++, C#, Ruby, PHP, Kotlin, Swift, Scala, Julia, Lua, Perl, Dart, Elixir, Haskell, Erlang, F#, VB, Shell, PowerShell, Markdown, common text | Encoding, lines, declarations, imports, and headings |
| Documents and media | PDF, DOCX, common images | Pages, paragraphs, tables, dimensions, and metadata keys |
| Other | Unknown text or binary | MIME type and generic file metadata |

When an optional parser is unavailable, the skill produces a useful `unsupported` or `error` explanation with explicit warnings.

### Requirements

- Python 3.9 or newer.
- Core indexing, SQLite/archive/XML/HTML/notebook parsing, and most source analysis use the standard library only.
- For full format coverage, install:

```bash
python -m pip install pandas openpyxl pyarrow PyYAML pypdf Pillow tomli
```

- Deep RDS/RData inspection requires `Rscript` and the R package `jsonlite`.

Rscript is resolved from:

1. `R_SCRIPT_EXE`;
2. `Rscript` on `PATH`;
3. common Windows R installation directories.

### Usage (language- and platform-agnostic)

`<skill-dir>` below means the installed `file-brief` skill directory. Any task — data analysis, a web project, a config pipeline, a migration script — follows the same workflow.

#### Choose a task root

`--task-root` should be the highest sensible directory that contains the inputs, scripts, and outputs for one large task.

- Prefer a root explicitly provided by the user.
- Otherwise use the current working directory.
- Do not infer a Git root.
- Every cataloged path must remain inside the task root.

#### Create the first catalog

```bash
python "<skill-dir>/scripts/file_catalog.py" catalog --task-root "/work/my-task"
```

With no explicit path, `catalog` recursively processes the task while excluding `.git`, `.file-catalog`, dependency folders, virtual environments, and caches.

#### Look up an input before starting work

```bash
python "<skill-dir>/scripts/file_catalog.py" lookup \
  --task-root "/work/my-task" \
  "data/observations.csv"
```

When the result is `fresh`, read the returned Markdown explanation instead of probing the source again. Append `--json` for machine-readable output.

#### Refresh a new or changed file

```bash
python "<skill-dir>/scripts/file_catalog.py" catalog \
  --task-root "/work/my-task" \
  "data/observations.csv"
```

The skill first compares file size and high-resolution modification time. It recalculates SHA-256 and reparses only missing or changed (or parser-version-upgraded) entries.

#### Search across subdirectories and inspect the catalog

```bash
python "<skill-dir>/scripts/file_catalog.py" search \
  --task-root "/work/my-task" \
  "species"

python "<skill-dir>/scripts/file_catalog.py" info \
  --task-root "/work/my-task"
```

Search covers relative paths, file names, formats, summaries, fields, keys, and other structural identifiers. `info` summarizes entries by status and format. Skip unwanted names with `--exclude "cache,tmp.sqlite"`.

### `.file-catalog` layout

```text
<task-root>/
└── .file-catalog/
    ├── INDEX.md
    ├── documents/
    │   └── <relative-path-hash>.md
    ├── catalog.sqlite3
    └── .gitignore
```

- `INDEX.md`: compact index for human browsing.
- `documents/`: one current explanation per task-relative path.
- `catalog.sqlite3`: machine index used by `lookup` and `search`.
- `.gitignore`: ignores SQLite, locks, and temporary files while leaving Markdown trackable.

### Status reference

| Status | Meaning | Recommended action |
|---|---|---|
| `fresh` | Explanation exists and size/mtime still match | Read the explanation |
| `stale` | Source changed after the last analysis | Run `catalog` |
| `missing` | Source or explanation is absent | Check the path, then catalog an existing source |
| `unsupported` | No deep parser matched, but generic metadata exists | Use the metadata or inspect manually if required |
| `error` | Deep parsing failed and a fallback explanation was produced | Read the warning, fix dependencies or the file, then refresh |

### Example structures

A SQLite explanation records table shape only, never cell values:

```json
{
  "table_count": 2,
  "tables": [
    {"name": "measurements", "column_count": 3, "row_count": 1250,
     "columns": [
       {"name": "species", "declared_type": "TEXT", "notnull": true}
     ]}
  ]
}
```

A ZIP explanation lists members and a type histogram without reading contents:

```json
{
  "member_count": 42,
  "member_names": ["data/a.csv", "data/b.csv", "README.md"],
  "extension_histogram": [{"extension": ".csv", "count": 2}]
}
```

A CSV explanation records the detected delimiter:

```json
{
  "delimiter": ";",
  "column_count": 3,
  "columns": [{"name": "species", "dtype": "object", "missing_percent_in_sample": 1.2}]
}
```

These are structural illustrations, not real input records.

### Recommended workflow

```text
Choose task root
  → lookup the inputs
  → fresh: read explanations
  → missing/stale: catalog, then read explanations
  → inspect raw sources only if the explanations are insufficient
  → keep production code focused on the actual task
```

Do not reintroduce one-off schema prints, sample dumps, or format probes into production scripts.

### What changed in v2 and where it is heading

Implemented in v2:

- **Platform-neutral**: one `SKILL.md` works with OpenAI Codex, Claude Code, and DeepSeek Harness; `install.ps1` / `install.sh` provide one-shot installation.
- **Wider format coverage**: SQLite, ZIP/JAR/APK, TAR/TGZ, GZIP, XML, HTML, Jupyter notebooks, Stata — all standard-library-only except Stata.
- **Generalization**: automatic delimiter detection for delimited tables; table-driven structural extraction for 20+ programming languages; the workflow is independent of language or tech stack.
- **Engineering**: `--json` machine-readable output, `info` subcommand, `--exclude` scan filters, bounded parallel parsing, parser versioning (upgrades re-analyze old entries), and search wildcard escaping.

Future directions (contributions welcome):

- Data formats: HDF5/NetCDF, SAS/SPSS, geospatial (GeoJSON/Shapefile) deep parsing.
- Structure: directory-level aggregate explanations; a cross-task global index.
- Semantics: an optional LLM summary layer that derives field semantics without storing raw values.
- Formats: preflight support for emerging AI-native file formats.

### Troubleshooting and FAQ

**Why is the exact CSV row count missing?**  
Delimited files use bounded sampling by default so a structural preflight does not fully scan a very large text table. The explanation records the sampling scope.

**Why does an Excel file report `error`?**  
Legacy XLS files or specialized workbooks may require an additional pandas engine. Install the relevant engine and rerun `catalog`.

**Why did TOML fall back?**  
Python 3.11+ includes `tomllib`; Python 3.9/3.10 require `tomli`.

**Why does R data inspection fail?**  
Confirm that `Rscript` is available and run `Rscript -e "install.packages('jsonlite')"`. You may also set `R_SCRIPT_EXE` to the executable path.

**Does the SQLite analysis read my data?**  
No. SQLite is opened read-only with `query_only` enabled; only `sqlite_master` schema objects and row counts are read, never cell values.

**Can `.file-catalog` be committed?**  
Yes. Commit `INDEX.md` and `documents/` when useful. SQLite and temporary files are ignored by the generated `.gitignore`.

**Can private values leak into explanations?**  
The implementation intentionally omits rows, cells, paragraph text, archive member contents, category values, and code snippets. Organizations handling highly sensitive data should still review generated Markdown before publishing it.

## License

MIT. See [LICENSE](LICENSE).
