#!/usr/bin/env python
"""
===============================================================================
代码介绍
===============================================================================
输入：
  1. 子命令 catalog、lookup、search 或 info。
  2. --task-root 指定“大任务”根目录；省略时使用当前工作目录。
  3. catalog/lookup 接受任务根目录内的文件或目录路径；search 接受检索词。
  4. 所有子命令支持 --json 输出；catalog/lookup 支持 --exclude 附加排除名。

输出：
  - 标准输出只返回受 --limit 限制的简短状态行（或 --json 的机器可读记录），
    适合 Agent 直接读取。
  - catalog 在 <task-root>/.file-catalog/ 中写入：
      INDEX.md
      documents/<任务相对路径哈希>.md
      catalog.sqlite3
      .gitignore
  - Markdown 说明包含路径、格式、结构、统计和解析限制，不保存数据行、单元格样例、
    正文段落、类别值或源代码片段。

作用：
  把每次任务都会重复出现的“先检查输入文件”步骤封装为可复用工具。Agent 先调用
  lookup；说明缺失或过期时再调用 catalog；之后直接使用说明文档完成任务设计，
  避免在生产脚本中混入冗长且不可复用的文件探查代码。技能本身与具体 Agent 平台
  （OpenAI Codex、Claude Code、DeepSeek Harness 等）无关：任何能够执行 Python
  命令的 Agent 都可以按相同工作流使用。

设计逻辑：
  - 以任务内相对路径作为稳定身份，因此整个任务文件夹移动后仍能匹配。
  - 以大小和 mtime_ns 快速判断新鲜度；需要更新时再流式计算 SHA-256。
  - 同一任务内 SHA-256 相同的文件复用已有结构结果。
  - 解析器按格式分层；缺少依赖或格式未知时生成通用说明和明确警告。
  - 所有读取均采用流式读取或有界采样；R 数据由同目录 inspect_r_data.R 处理。
  - 新增格式解析器只依赖标准库：SQLite（表/列/行数）、ZIP/JAR/APK（成员清单）、
    TAR/GZIP（成员与类型）、XML/HTML（标签与属性键）、Jupyter notebook（单元格
    统计）；CSV/TSV 自动探测分隔符；更多编程语言按扩展名做表驱动结构提取。
  - SQLite 使用 WAL、busy_timeout 和事务；Markdown 使用临时文件 + os.replace 原子写入。
  - catalog 的文件解析使用有界线程池并行，数据库写入仍串行，保证确定性输出。

主要函数：
  resolve_task_root()      解析并验证任务根目录。
  gather_files()           递归收集任务文件并应用排除规则。
  sha256_file()            流式计算内容哈希。
  detect_delimiter()       探测分隔文本的分隔符。
  analyze_file()           按扩展名路由到具体解析器。
  catalog_files()          增量建档、内容复用、缺失标记和索引更新。
  lookup_files()           判断说明的新鲜度。
  search_catalog()         查询任务内 SQLite 索引。
  catalog_info()           输出任务目录的统计信息。
  render_document()        生成不含原始样例的 Markdown 说明。
  render_index()           生成可跟踪的任务级 INDEX.md。
  main()                   解析命令行并调度子命令。

调用方式：
  python file_catalog.py catalog --task-root "D:\\project"
  python file_catalog.py catalog --task-root "D:\\project" "data\\input.csv"
  python file_catalog.py lookup --task-root "D:\\project" "data\\input.csv" --json
  python file_catalog.py search --task-root "D:\\project" "species"
  python file_catalog.py info --task-root "D:\\project"
===============================================================================
"""

from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import gzip
import hashlib
import html.parser
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree


CATALOG_VERSION = 2
SAMPLE_BYTES = 2 * 1024 * 1024
TABLE_SAMPLE_ROWS = 1000
MAX_STRUCTURAL_ITEMS = 200
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
MAX_XML_ELEMENTS = 2_000_000
MAX_PARSE_WORKERS = 4
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".file-catalog",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "dist",
    "build",
    ".idea",
    ".gradle",
    ".tox",
    ".nox",
    ".eggs",
    ".terraform",
    ".next",
    ".nuxt",
    ".svelte-kit",
}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".sql",
    ".ini",
    ".cfg",
    ".conf",
}
CODE_EXTENSIONS = {
    ".py",
    ".r",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".sh",
    ".ps1",
    ".rb",
    ".php",
    ".kt",
    ".kts",
    ".swift",
    ".scala",
    ".jl",
    ".lua",
    ".pl",
    ".pm",
    ".dart",
    ".groovy",
    ".ex",
    ".exs",
    ".hs",
    ".erl",
    ".hrl",
    ".fs",
    ".fsx",
    ".vb",
}
ARCHIVE_EXTENSIONS = {".zip", ".jar", ".war", ".ear", ".apk", ".whl", ".egg"}
TAR_EXTENSIONS = {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".db3"}
XML_EXTENSIONS = {".xml", ".xhtml", ".xsd", ".xsl", ".xslt", ".svg", ".kml", ".gpx"}
HTML_EXTENSIONS = {".html", ".htm"}

# 语言无关的源代码结构提取表：按扩展名提供声明/导入正则。
# 所有捕获组只取“结构名称”（函数名、类名、导入路径），不保存源码片段。
LANGUAGE_PROFILES: Dict[str, Dict[str, str]] = {
    ".java": {
        "format_name": "Java source",
        "decl_re": r"(?m)^\s*(?:public|protected|private|static|final|abstract|native|synchronized|\s)*"
        r"(?:class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)",
        "package_re": r"(?m)^\s*package\s+([\w.]+)",
    },
    ".go": {
        "format_name": "Go source",
        "decl_re": r"(?m)^\s*(?:func|type|struct|interface)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+(?:[\w.]+\s+)??\"([^\"]+)\"|^\s*\"([^\"]+)\"",
        "package_re": r"(?m)^\s*package\s+([a-z]\w*)",
    },
    ".rs": {
        "format_name": "Rust source",
        "decl_re": r"(?m)^\s*(?:pub\s+)?(?:fn|struct|enum|impl|trait|type|mod|const|static)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:pub\s+)?use\s+([\w:]+)",
    },
    ".c": {
        "format_name": "C source",
        "decl_re": r"(?m)^\s*(?:static\s+|inline\s+|const\s+)*[A-Za-z_]\w*\s*\*?\s*"
        r"([A-Za-z_]\w*)\s*\(",
        "import_re": r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]",
    },
    ".h": {
        "format_name": "C/C++ header",
        "decl_re": r"(?m)^\s*(?:class|struct|enum|union|typedef)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]",
    },
    ".cpp": {
        "format_name": "C++ source",
        "decl_re": r"(?m)^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct|enum|union|namespace|"
        r"inline\s+)?(?:[A-Za-z_]\w*\s*::\s*)*([A-Za-z_]\w*)\s*(?:\(|\{)",
        "import_re": r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]",
    },
    ".hpp": {
        "format_name": "C++ header",
        "decl_re": r"(?m)^\s*(?:class|struct|enum|union|namespace|template)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]",
    },
    ".cs": {
        "format_name": "C# source",
        "decl_re": r"(?m)^\s*(?:public|private|protected|internal|static|sealed|abstract|"
        r"partial|readonly|async|\s)*"
        r"(?:class|struct|interface|enum|record|namespace)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*using\s+([\w.]+)\s*;",
    },
    ".rb": {
        "format_name": "Ruby source",
        "decl_re": r"(?m)^\s*(?:def|class|module)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*require(?:_relative)?\s+[\"']([^\"']+)",
    },
    ".php": {
        "format_name": "PHP source",
        "decl_re": r"(?m)^\s*(?:function|class|interface|trait|enum)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:namespace|use)\s+([A-Za-z_\\][\w\\]*)",
    },
    ".kt": {
        "format_name": "Kotlin source",
        "decl_re": r"(?m)^\s*(?:fun|class|data\s+class|object|interface|enum\s+class|"
        r"sealed\s+class)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+([\w.]+)",
    },
    ".kts": {
        "format_name": "Kotlin script",
        "decl_re": r"(?m)^\s*(?:fun|class|data\s+class|object|interface)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+([\w.]+)",
    },
    ".swift": {
        "format_name": "Swift source",
        "decl_re": r"(?m)^\s*(?:public|private|internal|fileprivate|open|\s)*"
        r"(?:func|class|struct|enum|protocol|extension)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+([\w.]+)",
    },
    ".scala": {
        "format_name": "Scala source",
        "decl_re": r"(?m)^\s*(?:def|class|object|trait|case\s+class|enum)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+([\w.]+)",
    },
    ".jl": {
        "format_name": "Julia source",
        "decl_re": r"(?m)^\s*(?:function|macro|struct|mutable\s+struct|abstract\s+type)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:using|import)\s+([\w.]+)",
    },
    ".lua": {
        "format_name": "Lua source",
        "decl_re": r"(?m)^\s*function\s+([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*)",
        "import_re": r"(?m)^\s*require\s*\(\s*[\"']([^\"']+)",
    },
    ".pl": {
        "format_name": "Perl source",
        "decl_re": r"(?m)^\s*sub\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*use\s+([A-Za-z_:]+)",
    },
    ".pm": {
        "format_name": "Perl module",
        "decl_re": r"(?m)^\s*sub\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*use\s+([A-Za-z_:]+)",
    },
    ".dart": {
        "format_name": "Dart source",
        "decl_re": r"(?m)^\s*(?:class|enum|mixin|extension)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+[\"']([^\"']+)",
    },
    ".groovy": {
        "format_name": "Groovy source",
        "decl_re": r"(?m)^\s*(?:def|class|interface|enum|trait)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*import\s+([\w.]+)",
    },
    ".ex": {
        "format_name": "Elixir source",
        "decl_re": r"(?m)^\s*defp?\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:use|import|require)\s+([\w.]+)",
    },
    ".exs": {
        "format_name": "Elixir script",
        "decl_re": r"(?m)^\s*defp?\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:use|import|require)\s+([\w.]+)",
    },
    ".hs": {
        "format_name": "Haskell source",
        "decl_re": r"(?m)^\s*[A-Za-z_][\w']*\s*::",
        "import_re": r"(?m)^\s*import\s+(?:qualified\s+)?([A-Za-z_.]+)",
    },
    ".erl": {
        "format_name": "Erlang source",
        "decl_re": r"(?m)^\s*([a-z][\w@]*)\s*\([^)]*\)\s*->",
        "import_re": r"(?m)^\s*-include(?:_lib)?\s*\(\s*[\"']([^\"']+)",
    },
    ".hrl": {
        "format_name": "Erlang header",
        "decl_re": r"(?m)^\s*([a-z][\w@]*)\s*\([^)]*\)\s*->",
        "import_re": r"(?m)^\s*-include(?:_lib)?\s*\(\s*[\"']([^\"']+)",
    },
    ".fs": {
        "format_name": "F# source",
        "decl_re": r"(?m)^\s*(?:let|type|module|namespace)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*open\s+([\w.]+)",
    },
    ".fsx": {
        "format_name": "F# script",
        "decl_re": r"(?m)^\s*(?:let|type|module)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*open\s+([\w.]+)",
    },
    ".vb": {
        "format_name": "Visual Basic source",
        "decl_re": r"(?m)^\s*(?:Sub|Function|Class|Module|Interface|Enum)\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*Imports\s+([\w.]+)",
    },
    ".sh": {
        "format_name": "Shell script",
        "decl_re": r"(?m)^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)",
        "import_re": r"(?m)^\s*\.\s+([^\s]+)|^\s*source\s+([^\s]+)",
    },
    ".ps1": {
        "format_name": "PowerShell script",
        "decl_re": r"(?m)^\s*function\s+([A-Za-z_]\w*)",
        "import_re": r"(?m)^\s*(?:Import-Module|using\s+module)\s+([^\s\"]+)",
    },
    ".js": {
        "format_name": "JavaScript source",
        "decl_re": r"(?m)^\s*(?:export\s+default\s+)?(?:function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        "import_re": r"(?m)^\s*import\s+.*?\s+from\s+[\"']([^\"']+)[\"']",
    },
    ".jsx": {
        "format_name": "JavaScript/JSX source",
        "decl_re": r"(?m)^\s*(?:export\s+default\s+)?(?:function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        "import_re": r"(?m)^\s*import\s+.*?\s+from\s+[\"']([^\"']+)[\"']",
    },
    ".ts": {
        "format_name": "TypeScript source",
        "decl_re": r"(?m)^\s*(?:export\s+default\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        "import_re": r"(?m)^\s*import\s+.*?\s+from\s+[\"']([^\"']+)[\"']",
    },
    ".tsx": {
        "format_name": "TypeScript/TSX source",
        "decl_re": r"(?m)^\s*(?:export\s+default\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        "import_re": r"(?m)^\s*import\s+.*?\s+from\s+[\"']([^\"']+)[\"']",
    },
    ".r": {
        "format_name": "R source",
        "decl_re": r"(?m)^\s*([A-Za-z.][A-Za-z0-9._]*)\s*(?:<-|=)\s*function\s*\(",
        "import_re": r"(?m)\b(?:library|require)\s*\(\s*[\"']?([A-Za-z0-9._]+)",
    },
}

# 通用声明正则（未单列语言的兜底），覆盖 def/func/fn/function/class/struct/interface 等。
GENERIC_DECL_RE = (
    r"(?m)^\s*(?:pub\s+|private\s+|protected\s+|internal\s+|static\s+|"
    r"final\s+|abstract\s+|async\s+|export\s+|default\s+)*"
    r"(?:def|func|fn|function|class|struct|interface|enum|trait|type|object|"
    r"module|package|mixin|extension|record)\s+([A-Za-z_]\w*)"
)


@dataclass
class Analysis:
    format_name: str
    analyzer: str
    status: str
    language: str
    summary_zh: str
    summary_en: str
    structure: Dict[str, Any]
    warnings: List[str]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def yaml_quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def clean_structural_name(value: Any, limit: int = 160) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    return text[:limit]


def detect_language(text: str) -> str:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk >= 4 and cjk >= max(1, int(latin * 0.12)):
        return "zh"
    if latin >= 8:
        return "en"
    return "zh"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_task_root(value: Optional[str]) -> Path:
    root = Path(value).expanduser() if value else Path.cwd()
    root = root.resolve()
    if not root.exists():
        raise ValueError(f"Task root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Task root is not a directory: {root}")
    return root


def resolve_input_path(raw: str, task_root: Path) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = task_root / candidate
    candidate = candidate.resolve(strict=False)
    if not is_within(candidate, task_root):
        raise ValueError(f"Input is outside task root: {candidate}")
    return candidate


def relative_key(path: Path, task_root: Path) -> str:
    return path.relative_to(task_root).as_posix()


def document_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.casefold().encode("utf-8")).hexdigest()[:24]


def gather_files(
    task_root: Path,
    raw_paths: Sequence[str],
    extra_excludes: Sequence[str] = (),
) -> Tuple[List[Path], List[Dict[str, str]], bool]:
    full_scan = len(raw_paths) == 0
    requested = [task_root] if full_scan else [resolve_input_path(x, task_root) for x in raw_paths]
    files: Dict[str, Path] = {}
    issues: List[Dict[str, str]] = []
    extra_excluded = {name.casefold() for name in extra_excludes if name.strip()}

    for requested_path in requested:
        if not requested_path.exists():
            issues.append(
                {
                    "status": "missing",
                    "source": str(requested_path),
                    "relative_path": (
                        relative_key(requested_path, task_root)
                        if is_within(requested_path, task_root)
                        else str(requested_path)
                    ),
                    "document": "",
                }
            )
            continue

        if requested_path.is_file():
            key = relative_key(requested_path, task_root)
            files[key.casefold()] = requested_path
            continue

        for current_root, directory_names, file_names in os.walk(
            str(requested_path), followlinks=False
        ):
            current = Path(current_root)
            directory_names[:] = [
                name
                for name in directory_names
                if name.casefold() not in EXCLUDED_DIR_NAMES
                and name.casefold() not in extra_excluded
                and not (current / name).is_symlink()
            ]
            for file_name in file_names:
                candidate = current / file_name
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if file_name.casefold() in extra_excluded:
                    continue
                key = relative_key(candidate.resolve(), task_root)
                files[key.casefold()] = candidate.resolve()

    ordered = sorted(files.values(), key=lambda p: relative_key(p, task_root).casefold())
    return ordered, issues, full_scan


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def decode_text_sample(raw: bytes, truncated: bool) -> Tuple[Optional[str], str, bool]:
    """Decode a bounded byte sample to text when it plausibly is text."""
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None, "binary", truncated

    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(encoding)
            printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
            if not text or printable / max(1, len(text)) >= 0.8:
                return text, encoding, truncated
        except UnicodeDecodeError:
            continue

    try:
        text = raw.decode("latin-1")
        printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
        if not text or printable / max(1, len(text)) >= 0.9:
            return text, "latin-1", truncated
    except UnicodeDecodeError:
        pass
    return None, "binary", truncated


def read_text_sample(path: Path, max_bytes: int = SAMPLE_BYTES) -> Tuple[Optional[str], str, bool]:
    raw = path.open("rb").read(max_bytes)
    truncated = path.stat().st_size > len(raw)
    return decode_text_sample(raw, truncated)


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def structural_schema(value: Any, depth: int = 0) -> Dict[str, Any]:
    result: Dict[str, Any] = {"type": json_type(value)}
    if depth >= 4:
        result["truncated_depth"] = True
        return result

    if isinstance(value, dict):
        keys = list(value.keys())
        selected = keys[:MAX_STRUCTURAL_ITEMS]
        result["key_count"] = len(keys)
        result["keys"] = {
            clean_structural_name(key): structural_schema(value[key], depth + 1)
            for key in selected
        }
        result["truncated_keys"] = len(keys) > len(selected)
    elif isinstance(value, list):
        sample = value[:100]
        result["item_count"] = len(value)
        result["sampled_item_count"] = len(sample)
        types = sorted({json_type(item) for item in sample})
        result["item_types"] = types
        if sample:
            representatives: Dict[str, Any] = {}
            for item in sample:
                kind = json_type(item)
                if kind not in representatives:
                    representatives[kind] = structural_schema(item, depth + 1)
            result["item_schemas"] = representatives
    return result


def column_structure(frame: Any) -> List[Dict[str, Any]]:
    columns: List[Dict[str, Any]] = []
    for name in list(frame.columns)[:MAX_STRUCTURAL_ITEMS]:
        series = frame[name]
        non_missing = series.dropna()
        columns.append(
            {
                "name": clean_structural_name(name),
                "dtype": str(series.dtype),
                "missing_in_sample": int(series.isna().sum()),
                "missing_percent_in_sample": round(float(series.isna().mean() * 100), 3),
                "approximate_unique_in_sample": int(non_missing.nunique(dropna=True)),
            }
        )
    return columns


def detect_delimiter(path: Path, hints: Sequence[str] = ()) -> str:
    """Detect the most consistent field delimiter from a bounded text sample.

    Scores candidates by how many sampled lines split into the same nonzero
    number of fields.  Quotes are honored via csv.reader so commas inside
    quoted fields do not distort the score.
    """
    candidates = list(hints) + [",", "\t", ";", "|"]
    seen: Set[str] = set()
    unique_candidates: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)

    sample = path.open("rb").read(SAMPLE_BYTES)
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = sample.decode("latin-1", errors="replace")
    lines = text.splitlines()[:200]

    best_delimiter = unique_candidates[0]
    best_score = -1.0
    for delimiter in unique_candidates:
        consistent_lines = 0
        column_totals = 0
        inspected = 0
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = next(csv.reader([line], delimiter=delimiter))
            except csv.Error:
                continue
            inspected += 1
            column_totals += len(fields)
            if len(fields) > 1:
                consistent_lines += 1
        if inspected == 0:
            continue
        score = consistent_lines + (column_totals / max(1, inspected)) * 0.01
        if score > best_score:
            best_score = score
            best_delimiter = delimiter
    return best_delimiter


def analyze_delimited(path: Path, separator: Optional[str] = None) -> Analysis:
    import pandas as pd

    if separator is None:
        hints: Sequence[str] = []
        if path.suffix.casefold() in {".tsv", ".tab"}:
            hints = ["\t"]
        separator = detect_delimiter(path, hints)

    warnings: List[str] = []
    last_error: Optional[Exception] = None
    frame = None
    encoding = ""
    for candidate_encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            frame = pd.read_csv(
                path,
                sep=separator,
                nrows=TABLE_SAMPLE_ROWS,
                encoding=candidate_encoding,
                low_memory=False,
            )
            encoding = candidate_encoding
            break
        except Exception as error:
            last_error = error
    if frame is None:
        raise RuntimeError(f"Delimited parser failed: {last_error}")

    names = " ".join(clean_structural_name(x) for x in frame.columns)
    language = detect_language(names)
    structure = {
        "encoding": encoding,
        "delimiter": "tab" if separator == "\t" else separator,
        "sample_rows": int(len(frame)),
        "row_count": "not fully counted",
        "column_count": int(len(frame.columns)),
        "columns": column_structure(frame),
        "truncated_columns": len(frame.columns) > MAX_STRUCTURAL_ITEMS,
    }
    warnings.append(
        f"Row and column statistics use at most the first {TABLE_SAMPLE_ROWS} records."
    )
    return Analysis(
        format_name="TSV" if separator == "\t" else "CSV",
        analyzer="pandas-delimited",
        status="fresh",
        language=language,
        summary_zh=f"分隔文本表格；已采样 {len(frame)} 行并识别 {len(frame.columns)} 个字段。",
        summary_en=f"Delimited table; sampled {len(frame)} rows and identified {len(frame.columns)} fields.",
        structure=structure,
        warnings=warnings,
    )


def analyze_excel(path: Path) -> Analysis:
    import pandas as pd

    workbook = pd.ExcelFile(path)
    sheet_names = list(workbook.sheet_names)
    sheets: List[Dict[str, Any]] = []
    language_material: List[str] = sheet_names.copy()
    for sheet_name in sheet_names[:50]:
        frame = workbook.parse(sheet_name=sheet_name, nrows=500)
        language_material.extend(str(x) for x in frame.columns)
        sheets.append(
            {
                "name": clean_structural_name(sheet_name),
                "sample_rows": int(len(frame)),
                "column_count": int(len(frame.columns)),
                "columns": column_structure(frame),
                "truncated_columns": len(frame.columns) > MAX_STRUCTURAL_ITEMS,
            }
        )
    language = detect_language(" ".join(language_material))
    return Analysis(
        format_name="Excel workbook",
        analyzer="pandas-excel",
        status="fresh",
        language=language,
        summary_zh=f"Excel 工作簿；识别 {len(sheet_names)} 个工作表并提取字段结构。",
        summary_en=f"Excel workbook; identified {len(sheet_names)} worksheets and their field structures.",
        structure={
            "sheet_count": len(sheet_names),
            "sheets": sheets,
            "truncated_sheets": len(sheet_names) > len(sheets),
            "statistics_scope": "up to 500 rows per sheet",
        },
        warnings=["Worksheet statistics are sample-based and do not contain cell values."],
    )


def analyze_parquet(path: Path) -> Analysis:
    import pyarrow.parquet as parquet

    metadata = parquet.ParquetFile(path)
    schema = metadata.schema_arrow
    fields = [
        {"name": clean_structural_name(field.name), "type": str(field.type), "nullable": field.nullable}
        for field in list(schema)[:MAX_STRUCTURAL_ITEMS]
    ]
    language = detect_language(" ".join(field["name"] for field in fields))
    return Analysis(
        format_name="Parquet",
        analyzer="pyarrow-parquet-metadata",
        status="fresh",
        language=language,
        summary_zh=f"Parquet 列式数据；元数据记录 {metadata.metadata.num_rows} 行、{len(schema)} 个字段。",
        summary_en=f"Parquet columnar data; metadata reports {metadata.metadata.num_rows} rows and {len(schema)} fields.",
        structure={
            "row_count": metadata.metadata.num_rows,
            "row_group_count": metadata.metadata.num_row_groups,
            "column_count": len(schema),
            "columns": fields,
            "truncated_columns": len(schema) > len(fields),
        },
        warnings=[],
    )


def analyze_feather(path: Path) -> Analysis:
    import pyarrow as pa
    import pyarrow.ipc as ipc

    source = pa.memory_map(str(path), "r")
    reader = ipc.open_file(source)
    schema = reader.schema
    fields = [
        {"name": clean_structural_name(field.name), "type": str(field.type), "nullable": field.nullable}
        for field in list(schema)[:MAX_STRUCTURAL_ITEMS]
    ]
    language = detect_language(" ".join(field["name"] for field in fields))
    return Analysis(
        format_name="Feather/Arrow IPC",
        analyzer="pyarrow-ipc-metadata",
        status="fresh",
        language=language,
        summary_zh=f"Feather/Arrow 文件；识别 {len(schema)} 个字段和 {reader.num_record_batches} 个记录批次。",
        summary_en=f"Feather/Arrow file; identified {len(schema)} fields and {reader.num_record_batches} record batches.",
        structure={
            "record_batch_count": reader.num_record_batches,
            "column_count": len(schema),
            "columns": fields,
            "truncated_columns": len(schema) > len(fields),
        },
        warnings=["Row count was not materialized to avoid loading record batches."],
    )


def analyze_json(path: Path, json_lines: bool) -> Analysis:
    size = path.stat().st_size
    warnings: List[str] = []
    if json_lines:
        records: List[Any] = []
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= TABLE_SAMPLE_ROWS:
                    warnings.append(
                        f"Only the first {TABLE_SAMPLE_ROWS} JSONL records were parsed."
                    )
                    break
                if line.strip():
                    records.append(json.loads(line))
        value: Any = records
        format_name = "JSON Lines"
    else:
        if size > MAX_JSON_BYTES:
            raise RuntimeError(
                f"JSON file exceeds bounded parse limit of {MAX_JSON_BYTES} bytes."
            )
        with path.open("r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
        format_name = "JSON"

    structure = structural_schema(value)
    structural_text = json.dumps(structure, ensure_ascii=False)
    language = detect_language(structural_text)
    return Analysis(
        format_name=format_name,
        analyzer="python-json",
        status="fresh",
        language=language,
        summary_zh=f"{format_name} 结构化数据；已提取键、嵌套层级和元素类型。",
        summary_en=f"{format_name} structured data; extracted keys, nesting, and element types.",
        structure=structure,
        warnings=warnings,
    )


def analyze_yaml(path: Path) -> Analysis:
    import yaml

    if path.stat().st_size > MAX_JSON_BYTES:
        raise RuntimeError(
            f"YAML file exceeds bounded parse limit of {MAX_JSON_BYTES} bytes."
        )
    with path.open("r", encoding="utf-8-sig") as handle:
        value = yaml.safe_load(handle)
    structure = structural_schema(value)
    structural_text = json.dumps(structure, ensure_ascii=False)
    return Analysis(
        format_name="YAML",
        analyzer="pyyaml",
        status="fresh",
        language=detect_language(structural_text),
        summary_zh="YAML 配置或结构化数据；已提取键、嵌套层级和元素类型。",
        summary_en="YAML configuration or structured data; extracted keys, nesting, and element types.",
        structure=structure,
        warnings=[],
    )


def analyze_toml(path: Path) -> Analysis:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise RuntimeError(
            f"TOML file exceeds bounded parse limit of {MAX_JSON_BYTES} bytes."
        )
    try:
        import tomllib as toml_reader  # type: ignore
    except ImportError:
        try:
            import tomli as toml_reader  # type: ignore
        except ImportError as error:
            raise RuntimeError("Neither tomllib nor tomli is available.") from error
    with path.open("rb") as handle:
        value = toml_reader.load(handle)
    structure = structural_schema(value)
    structural_text = json.dumps(structure, ensure_ascii=False)
    return Analysis(
        format_name="TOML",
        analyzer="toml",
        status="fresh",
        language=detect_language(structural_text),
        summary_zh="TOML 配置；已提取节、键和嵌套结构。",
        summary_en="TOML configuration; extracted sections, keys, and nesting.",
        structure=structure,
        warnings=[],
    )


def analyze_python_source(path: Path, text: str, encoding: str, truncated: bool) -> Analysis:
    if truncated:
        raise RuntimeError("Python source exceeds the bounded parser size.")
    tree = ast.parse(text)
    imports: List[str] = []
    functions: List[str] = []
    classes: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    structure = {
        "encoding": encoding,
        "line_count": len(text.splitlines()),
        "imports": [clean_structural_name(x) for x in imports[:MAX_STRUCTURAL_ITEMS]],
        "functions": [clean_structural_name(x) for x in functions[:MAX_STRUCTURAL_ITEMS]],
        "classes": [clean_structural_name(x) for x in classes[:MAX_STRUCTURAL_ITEMS]],
        "module_docstring_present": ast.get_docstring(tree) is not None,
    }
    return Analysis(
        format_name="Python source",
        analyzer="python-ast",
        status="fresh",
        language=detect_language(text[:10000]),
        summary_zh=f"Python 源代码；识别 {len(functions)} 个函数、{len(classes)} 个类和 {len(imports)} 个导入。",
        summary_en=f"Python source; identified {len(functions)} functions, {len(classes)} classes, and {len(imports)} imports.",
        structure=structure,
        warnings=[],
    )


def analyze_code_or_text(path: Path) -> Analysis:
    text, encoding, truncated = read_text_sample(path, max_bytes=5 * 1024 * 1024)
    if text is None:
        raise RuntimeError("The file did not pass text decoding checks.")
    extension = path.suffix.casefold()
    if extension == ".py":
        return analyze_python_source(path, text, encoding, truncated)

    lines = text.splitlines()
    structure: Dict[str, Any] = {
        "encoding": encoding,
        "sample_line_count": len(lines),
        "sample_truncated": truncated,
    }
    format_name = "Text"
    analyzer = "bounded-text-structure"

    if extension in {".md", ".markdown", ".rst"}:
        headings = [
            clean_structural_name(match.group(2))
            for line in lines
            for match in [re.match(r"^(#{1,6})\s+(.+?)\s*$", line)]
            if match
        ]
        structure["headings"] = headings[:MAX_STRUCTURAL_ITEMS]
        structure["heading_count_in_sample"] = len(headings)
        format_name = "Markdown/text document"
    elif extension in CODE_EXTENSIONS:
        profile = LANGUAGE_PROFILES.get(extension)
        if profile:
            return analyze_generic_source(path, text, encoding, truncated)
        declarations = re.findall(GENERIC_DECL_RE, text)
        structure["declarations"] = [
            clean_structural_name(x) for x in declarations[:MAX_STRUCTURAL_ITEMS]
        ]
        format_name = f"{extension.lstrip('.').upper()} source"
        analyzer = "generic-source-structure"

    language = detect_language(text[:20000])
    return Analysis(
        format_name=format_name,
        analyzer=analyzer,
        status="fresh",
        language=language,
        summary_zh=f"{format_name}；已记录编码、行数和可识别的结构性名称。",
        summary_en=f"{format_name}; recorded encoding, line counts, and recognizable structural names.",
        structure=structure,
        warnings=(
            ["Only a bounded prefix was inspected; counts may be incomplete."]
            if truncated
            else []
        ),
    )


def analyze_pdf(path: Path) -> Analysis:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    metadata_keys = sorted(str(key) for key in (reader.metadata or {}).keys())
    first_page_box: Optional[List[float]] = None
    if reader.pages:
        box = reader.pages[0].mediabox
        first_page_box = [float(box.width), float(box.height)]
    return Analysis(
        format_name="PDF",
        analyzer="pypdf-metadata",
        status="fresh",
        language="zh",
        summary_zh=f"PDF 文档；识别 {len(reader.pages)} 页，仅记录页面和元数据结构。",
        summary_en=f"PDF document; identified {len(reader.pages)} pages and recorded metadata structure only.",
        structure={
            "page_count": len(reader.pages),
            "encrypted": bool(reader.is_encrypted),
            "metadata_keys": metadata_keys,
            "first_page_size_points": first_page_box,
        },
        warnings=["Document text was not copied into the catalog."],
    )


def analyze_docx(path: Path) -> Analysis:
    namespaces = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    }
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
        paragraphs = root.findall(".//w:p", namespaces)
        tables = root.findall(".//w:tbl", namespaces)
        heading_styles: Dict[str, int] = {}
        language_material: List[str] = []
        for paragraph in paragraphs[:5000]:
            style = paragraph.find("./w:pPr/w:pStyle", namespaces)
            if style is not None:
                style_name = style.attrib.get(
                    f"{{{namespaces['w']}}}val", ""
                )
                if style_name.lower().startswith("heading"):
                    heading_styles[style_name] = heading_styles.get(style_name, 0) + 1
            for text_node in paragraph.findall(".//w:t", namespaces):
                if text_node.text and sum(len(x) for x in language_material) < 20000:
                    language_material.append(text_node.text)
        names = set(archive.namelist())
    return Analysis(
        format_name="DOCX",
        analyzer="docx-zip-xml",
        status="fresh",
        language=detect_language(" ".join(language_material)),
        summary_zh=f"DOCX 文档；识别 {len(paragraphs)} 个段落和 {len(tables)} 个表格。",
        summary_en=f"DOCX document; identified {len(paragraphs)} paragraphs and {len(tables)} tables.",
        structure={
            "paragraph_count": len(paragraphs),
            "table_count": len(tables),
            "heading_style_counts": heading_styles,
            "has_headers": any(name.startswith("word/header") for name in names),
            "has_footers": any(name.startswith("word/footer") for name in names),
            "embedded_media_count": sum(
                name.startswith("word/media/") for name in names
            ),
        },
        warnings=["Paragraph and cell text was not copied into the catalog."],
    )


def analyze_image(path: Path) -> Analysis:
    from PIL import Image

    with Image.open(path) as image:
        structure = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "frame_count": getattr(image, "n_frames", 1),
            "metadata_keys": sorted(clean_structural_name(x) for x in image.info.keys()),
        }
        format_name = f"{image.format or path.suffix.lstrip('.').upper()} image"
    return Analysis(
        format_name=format_name,
        analyzer="pillow-metadata",
        status="fresh",
        language="zh",
        summary_zh=f"图像文件；尺寸为 {structure['width']}×{structure['height']}，模式为 {structure['mode']}。",
        summary_en=f"Image file; dimensions are {structure['width']}×{structure['height']} with mode {structure['mode']}.",
        structure=structure,
        warnings=["Pixel content and metadata values were not copied into the catalog."],
    )


def analyze_sqlite(path: Path) -> Analysis:
    """Read a SQLite schema read-only through the standard library.

    Only schema objects (table/view/index names, column names and declared
    types) and row counts are recorded.  No cell values are read.
    """
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise RuntimeError(f"SQLite open failed: {error}") from error
    try:
        connection.execute("PRAGMA query_only = ON")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 200"
        ).fetchall()
        views = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name LIMIT 200"
        ).fetchall()
        indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
            "ORDER BY name LIMIT 200"
        ).fetchall()
        described: List[Dict[str, Any]] = []
        truncated_tables = len(tables) > 100
        for (table_name,) in tables[:100]:
            columns = connection.execute(
                f'PRAGMA table_info("{table_name.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            row_count: Optional[int] = None
            try:
                row_count = connection.execute(
                    f'SELECT count(*) FROM "{table_name.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
            except sqlite3.Error:
                row_count = None
            described.append(
                {
                    "name": clean_structural_name(table_name),
                    "column_count": len(columns),
                    "row_count": row_count,
                    "columns": [
                        {
                            "name": clean_structural_name(column[1]),
                            "declared_type": clean_structural_name(column[2]),
                            "notnull": bool(column[3]),
                            "primary_key": bool(column[5]),
                        }
                        for column in columns[:MAX_STRUCTURAL_ITEMS]
                    ],
                    "truncated_columns": len(columns) > MAX_STRUCTURAL_ITEMS,
                }
            )
    except sqlite3.Error as error:
        raise RuntimeError(f"SQLite schema inspection failed: {error}") from error
    finally:
        connection.close()
    structure = {
        "table_count": len(tables),
        "view_count": len(views),
        "index_count": len(indexes),
        "tables": described,
        "truncated_tables": truncated_tables,
    }
    return Analysis(
        format_name="SQLite database",
        analyzer="sqlite-schema",
        status="fresh",
        language="zh",
        summary_zh=f"SQLite 数据库；识别 {len(tables)} 张表、{len(views)} 个视图和 {len(indexes)} 个索引。",
        summary_en=f"SQLite database; identified {len(tables)} tables, {len(views)} views, and {len(indexes)} indexes.",
        structure=structure,
        warnings=["Only schema objects and row counts were recorded; cell values were never read."],
    )


def analyze_zip_archive(path: Path) -> Analysis:
    """List archive members from the central directory without extracting."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        truncated = len(infos) > MAX_ARCHIVE_MEMBERS
        infos = infos[:MAX_ARCHIVE_MEMBERS]
        extension_counts: Dict[str, int] = {}
        top_level: Set[str] = set()
        duplicates = 0
        seen_names: Set[str] = set()
        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            name = info.filename
            total_uncompressed += info.file_size
            total_compressed += info.compress_size
            top_level.add(name.split("/", 1)[0])
            if name.casefold() in seen_names:
                duplicates += 1
            seen_names.add(name.casefold())
            extension = Path(name).suffix.casefold()
            extension_counts[extension or "(none)"] = (
                extension_counts.get(extension or "(none)", 0) + 1
            )
        top_extensions = sorted(
            extension_counts.items(), key=lambda item: item[1], reverse=True
        )[:20]
    structure = {
        "member_count": len(infos),
        "member_names": [
            clean_structural_name(info.filename) for info in infos[:MAX_STRUCTURAL_ITEMS]
        ],
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_bytes": total_compressed,
        "duplicate_name_count": duplicates,
        "encrypted_member_count": sum(1 for info in infos if info.flag_bits & 0x1),
        "top_level_entries": sorted(top_level)[:MAX_STRUCTURAL_ITEMS],
        "extension_histogram": [
            {"extension": name, "count": count}
            for name, count in top_extensions
        ],
        "truncated_members": truncated,
        "truncated_member_names": len(infos) > MAX_STRUCTURAL_ITEMS,
    }
    warnings = (
        [f"Archive has more than {MAX_ARCHIVE_MEMBERS} members; only the first batch was listed."]
        if truncated
        else []
    )
    warnings.append("Member contents were not extracted or read.")
    return Analysis(
        format_name="ZIP archive",
        analyzer="zipfile-central-directory",
        status="fresh",
        language="zh",
        summary_zh=f"ZIP 归档；列出 {len(infos)} 个成员及类型直方图。",
        summary_en=f"ZIP archive; listed {len(infos)} members and a type histogram.",
        structure=structure,
        warnings=warnings,
    )


def analyze_tar_archive(path: Path) -> Analysis:
    """Describe TAR/TAR.GZ/TAR.BZ2/TAR.XZ member lists without extraction."""
    type_labels = {
        tarfile.REGTYPE: "file",
        tarfile.AREGTYPE: "file",
        tarfile.DIRTYPE: "directory",
        tarfile.SYMTYPE: "symlink",
        tarfile.LNKTYPE: "hardlink",
        tarfile.CHRTYPE: "char-device",
        tarfile.BLKTYPE: "block-device",
        tarfile.FIFOTYPE: "fifo",
    }
    extension_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}
    top_level: Set[str] = set()
    total_size = 0
    member_count = 0
    truncated = False
    first_members: List[Any] = []
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive:
            if member_count >= MAX_ARCHIVE_MEMBERS:
                truncated = True
                break
            member_count += 1
            if len(first_members) < MAX_STRUCTURAL_ITEMS:
                first_members.append(member)
            label = type_labels.get(member.type, "other")
            type_counts[label] = type_counts.get(label, 0) + 1
            top_level.add(member.name.split("/", 1)[0])
            if member.isreg():
                total_size += member.size
                extension = Path(member.name).suffix.casefold()
                extension_counts[extension or "(none)"] = (
                    extension_counts.get(extension or "(none)", 0) + 1
                )
    top_extensions = sorted(
        extension_counts.items(), key=lambda item: item[1], reverse=True
    )[:20]
    structure = {
        "member_count": member_count,
        "member_names": [
            clean_structural_name(member.name) for member in first_members
        ],
        "total_regular_size_bytes": total_size,
        "member_type_counts": type_counts,
        "top_level_entries": sorted(top_level)[:MAX_STRUCTURAL_ITEMS],
        "extension_histogram": [
            {"extension": name, "count": count}
            for name, count in top_extensions
        ],
        "truncated_members": truncated,
        "truncated_member_names": len(first_members) > MAX_STRUCTURAL_ITEMS,
    }
    warnings = (
        [f"Archive has more than {MAX_ARCHIVE_MEMBERS} members; listing stopped early."]
        if truncated
        else []
    )
    warnings.append("Member contents were not extracted or read.")
    return Analysis(
        format_name="TAR archive",
        analyzer="tarfile-members",
        status="fresh",
        language="zh",
        summary_zh=f"TAR 归档；列出 {member_count} 个成员及类型分布。",
        summary_en=f"TAR archive; listed {member_count} members and type distribution.",
        structure=structure,
        warnings=warnings,
    )


def analyze_gzip_stream(path: Path) -> Analysis:
    """Describe a plain gzip stream (not a TAR) from its header and a bounded sample."""
    with gzip.open(path, "rb") as handle:
        header_name = getattr(handle, "name", None)
        if isinstance(header_name, bytes):
            header_name = header_name.decode("utf-8", errors="replace")
        raw = handle.read(SAMPLE_BYTES)
    text, encoding, sample_truncated = decode_text_sample(raw, truncated=True)
    structure: Dict[str, Any] = {
        "header_filename": clean_structural_name(header_name) if header_name else None,
        "compressed_size_bytes": path.stat().st_size,
        "decompressed_sample_bytes": len(raw),
        "sample_truncated": sample_truncated,
    }
    if text is not None:
        structure["decoded_sample"] = True
        structure["detected_encoding"] = encoding
        structure["sample_line_count"] = len(text.splitlines())
    else:
        structure["decoded_sample"] = False
    warnings = [
        "Only the first bounded decompressed sample was inspected; "
        "total decompressed size was not materialized."
    ]
    return Analysis(
        format_name="GZIP stream",
        analyzer="gzip-header-sample",
        status="fresh",
        language=detect_language(text[:20000]) if text else "zh",
        summary_zh="gzip 压缩流；记录了头部信息与有界解压样本结构。",
        summary_en="gzip stream; recorded header information and a bounded decompressed sample.",
        structure=structure,
        warnings=warnings,
    )


def analyze_xml(path: Path) -> Analysis:
    """Count XML tags, attribute keys, namespaces, and depth without keeping text."""
    tags: Dict[str, int] = {}
    attribute_keys: Set[str] = set()
    namespaces: Set[str] = set()
    max_depth = 0
    depth = 0
    element_count = 0
    truncated = False
    for event, element in ElementTree.iterparse(str(path), events=("start", "end")):
        if event == "start":
            element_count += 1
            if element_count > MAX_XML_ELEMENTS:
                truncated = True
                element.clear()
                break
            depth += 1
            max_depth = max(max_depth, depth)
            tag = element.tag
            if isinstance(tag, str) and tag.startswith("{"):
                namespace, _, local = tag[1:].partition("}")
                namespaces.add(namespace)
                tag = local
            tags[str(tag)] = tags.get(str(tag), 0) + 1
            if len(tags) <= 500:
                attribute_keys.update(clean_structural_name(key) for key in element.attrib.keys())
            element.clear()
        else:
            depth -= 1
    top_tags = sorted(tags.items(), key=lambda item: item[1], reverse=True)[:30]
    structure = {
        "root_tag": next(iter(tags), None),
        "element_count": element_count,
        "max_nesting_depth": max_depth,
        "namespace_count": len(namespaces),
        "distinct_tag_count": len(tags),
        "top_tags": [{"tag": name, "count": count} for name, count in top_tags],
        "attribute_keys": sorted(attribute_keys)[:MAX_STRUCTURAL_ITEMS],
        "truncated_elements": truncated,
    }
    warnings = (
        [f"XML has more than {MAX_XML_ELEMENTS} elements; counting stopped early."]
        if truncated
        else []
    )
    warnings.append("Text content and attribute values were not copied into the catalog.")
    return Analysis(
        format_name="XML document",
        analyzer="xml-iterparse-structure",
        status="fresh",
        language="zh",
        summary_zh=f"XML 文档；统计了 {element_count} 个元素、{len(namespaces)} 个命名空间和 {len(tags)} 种标签。",
        summary_en=f"XML document; counted {element_count} elements, {len(namespaces)} namespaces, and {len(tags)} distinct tags.",
        structure=structure,
        warnings=warnings,
    )


class _TagCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Dict[str, int] = {}
        self.heading_counts: Dict[str, int] = {}
        self.attribute_keys: Set[str] = set()
        self.counts = {
            "table": 0,
            "tr": 0,
            "td": 0,
            "th": 0,
            "link": 0,
            "img": 0,
            "script": 0,
            "style": 0,
            "form": 0,
            "input": 0,
            "iframe": 0,
        }
        self.has_title = False
        self.title_open = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.casefold()
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag in self.counts:
            self.counts[tag] += 1
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self.heading_counts[tag] = self.heading_counts.get(tag, 0) + 1
        if tag == "title":
            self.title_open = True
        if len(self.attribute_keys) < 500:
            self.attribute_keys.update(clean_structural_name(key) for key, _ in attrs)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self.title_open = False


def analyze_html(path: Path) -> Analysis:
    raw = path.open("rb").read(SAMPLE_BYTES)
    truncated = path.stat().st_size > len(raw)
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1", errors="replace")
    collector = _TagCollector()
    collector.feed(text)
    collector.close()
    top_tags = sorted(
        collector.tags.items(), key=lambda item: item[1], reverse=True
    )[:30]
    structure = {
        "encoding": encoding,
        "sample_truncated": truncated,
        "distinct_tag_count": len(collector.tags),
        "top_tags": [{"tag": name, "count": count} for name, count in top_tags],
        "heading_counts": collector.heading_counts,
        "structural_counts": collector.counts,
        "has_title": collector.has_title,
        "attribute_keys": sorted(collector.attribute_keys)[:MAX_STRUCTURAL_ITEMS],
    }
    warnings = (
        ["Only a bounded HTML prefix was inspected."] if truncated else []
    )
    warnings.append("Visible text and attribute values were not copied into the catalog.")
    return Analysis(
        format_name="HTML document",
        analyzer="htmlparser-structure",
        status="fresh",
        language=detect_language(text[:20000]),
        summary_zh=f"HTML 文档；统计了 {len(collector.tags)} 种标签及标题/表格/链接结构。",
        summary_en=f"HTML document; counted {len(collector.tags)} distinct tags plus heading/table/link structure.",
        structure=structure,
        warnings=warnings,
    )


def analyze_ipynb(path: Path) -> Analysis:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise RuntimeError(
            f"Notebook file exceeds bounded parse limit of {MAX_JSON_BYTES} bytes."
        )
    with path.open("r", encoding="utf-8-sig") as handle:
        notebook = json.load(handle)
    cells = notebook.get("cells", [])
    cell_types: Dict[str, int] = {}
    languages: Set[str] = set()
    executed_cells = 0
    for cell in cells:
        cell_type = str(cell.get("cell_type", "unknown"))
        cell_types[cell_type] = cell_types.get(cell_type, 0) + 1
        if cell.get("execution_count") is not None:
            executed_cells += 1
    metadata = notebook.get("metadata") or {}
    kernelspec = metadata.get("kernelspec") or {}
    language_info = metadata.get("language_info") or {}
    if kernelspec.get("language"):
        languages.add(str(kernelspec["language"]))
    if language_info.get("name"):
        languages.add(str(language_info["name"]))
    structure = {
        "nbformat": notebook.get("nbformat"),
        "nbformat_minor": notebook.get("nbformat_minor"),
        "cell_count": len(cells),
        "cell_type_counts": cell_types,
        "executed_cell_count": executed_cells,
        "languages": sorted(languages),
        "kernelspec_name": clean_structural_name(kernelspec.get("name"))
        if kernelspec.get("name")
        else None,
    }
    return Analysis(
        format_name="Jupyter notebook",
        analyzer="ipynb-structure",
        status="fresh",
        language="zh",
        summary_zh=f"Jupyter notebook；包含 {len(cells)} 个单元格，类型分布为 {cell_types}。",
        summary_en=f"Jupyter notebook; contains {len(cells)} cells with type distribution {cell_types}.",
        structure=structure,
        warnings=["Cell source, outputs, and display data were not copied into the catalog."],
    )


def analyze_stata(path: Path) -> Analysis:
    import pandas as pd

    reader = pd.read_stata(path, iterator=True)
    try:
        frame = reader.get_chunk(TABLE_SAMPLE_ROWS)
        variable_count = len(frame.columns)
    finally:
        reader.close()
    language = detect_language(" ".join(clean_structural_name(x) for x in frame.columns))
    structure = {
        "sample_rows": int(len(frame)),
        "column_count": variable_count,
        "columns": column_structure(frame),
        "truncated_columns": variable_count > MAX_STRUCTURAL_ITEMS,
    }
    return Analysis(
        format_name="Stata dataset",
        analyzer="pandas-stata",
        status="fresh",
        language=language,
        summary_zh=f"Stata 数据集；已采样 {len(frame)} 行并识别 {variable_count} 个变量。",
        summary_en=f"Stata dataset; sampled {len(frame)} rows and identified {variable_count} variables.",
        structure=structure,
        warnings=[
            "Value labels and cell values were not copied into the catalog.",
            f"Statistics use at most the first {TABLE_SAMPLE_ROWS} records.",
        ],
    )


def analyze_generic_source(path: Path, text: str, encoding: str, truncated: bool) -> Analysis:
    """Table-driven structural analysis for many programming languages."""
    extension = path.suffix.casefold()
    profile = LANGUAGE_PROFILES.get(extension, {})
    structure: Dict[str, Any] = {
        "encoding": encoding,
        "sample_line_count": len(text.splitlines()),
        "sample_truncated": truncated,
    }
    decl_pattern = profile.get("decl_re") or GENERIC_DECL_RE
    declarations = re.findall(decl_pattern, text)
    structure["declarations"] = [
        clean_structural_name(x) for x in declarations[:MAX_STRUCTURAL_ITEMS]
    ]
    if profile.get("import_re"):
        imports = re.findall(profile["import_re"], text)
        flattened = [item for match in imports for item in (match if isinstance(match, tuple) else (match,)) if item]
        structure["imports"] = [
            clean_structural_name(x) for x in flattened[:MAX_STRUCTURAL_ITEMS]
        ]
    if profile.get("package_re"):
        packages = re.findall(profile["package_re"], text)
        structure["packages"] = [
            clean_structural_name(x) for x in packages[:MAX_STRUCTURAL_ITEMS]
        ]
    format_name = profile.get("format_name") or f"{extension.lstrip('.').upper()} source"
    return Analysis(
        format_name=format_name,
        analyzer="generic-source-structure",
        status="fresh",
        language=detect_language(text[:20000]),
        summary_zh=f"{format_name}；已记录编码、行数和可识别的结构性名称。",
        summary_en=f"{format_name}; recorded encoding, line counts, and recognizable structural names.",
        structure=structure,
        warnings=(
            ["Only a bounded prefix was inspected; counts may be incomplete."]
            if truncated
            else []
        ),
    )


def locate_rscript() -> Optional[str]:
    environment_value = os.environ.get("R_SCRIPT_EXE")
    candidates = [
        environment_value,
        shutil.which("Rscript"),
    ]
    if os.name == "nt":
        windows_roots: List[Path] = []
        program_files = os.environ.get("ProgramFiles")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if program_files:
            windows_roots.append(Path(program_files) / "R")
        if local_app_data:
            windows_roots.append(Path(local_app_data) / "Programs" / "R")
        for root in windows_roots:
            if not root.is_dir():
                continue
            versions = sorted(
                (path for path in root.glob("R-*") if path.is_dir()),
                key=lambda path: path.name.casefold(),
                reverse=True,
            )
            for version in versions:
                candidates.extend(
                    [
                        str(version / "bin" / "Rscript.exe"),
                        str(version / "bin" / "x64" / "Rscript.exe"),
                    ]
                )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def analyze_r_data(path: Path) -> Analysis:
    rscript = locate_rscript()
    if not rscript:
        raise RuntimeError("Rscript could not be located.")
    helper = Path(__file__).with_name("inspect_r_data.R")
    completed = subprocess.run(
        [rscript, "--vanilla", str(helper), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    stdout = completed.stdout[:2_000_000]
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        stderr = completed.stderr[-1000:].strip()
        raise RuntimeError(f"R inspector returned invalid JSON: {stderr}") from error
    if completed.returncode != 0 or payload.get("status") != "ok":
        raise RuntimeError(str(payload.get("message", "R inspector failed.")))
    object_names = list((payload.get("objects") or {}).keys())
    language = detect_language(" ".join(object_names))
    return Analysis(
        format_name=str(payload.get("format", "R data")),
        analyzer="r-structure-helper",
        status="fresh",
        language=language,
        summary_zh=f"R 数据文件；识别 {len(object_names)} 个顶层对象并提取类、维度和字段结构。",
        summary_en=f"R data file; identified {len(object_names)} top-level objects and extracted classes, dimensions, and fields.",
        structure=payload,
        warnings=[str(x) for x in payload.get("warnings", [])],
    )


def generic_analysis(path: Path, reason: Optional[str] = None) -> Analysis:
    mime_type, encoding_hint = mimetypes.guess_type(str(path))
    text, encoding, truncated = read_text_sample(path)
    if text is not None:
        lines = text.splitlines()
        warnings = (
            ["Only a bounded text prefix was inspected."] if truncated else []
        )
        if reason:
            warnings.append(reason)
        return Analysis(
            format_name=mime_type or "Generic text",
            analyzer="generic-text-metadata",
            status="fresh" if reason is None else "error",
            language=detect_language(text[:20000]),
            summary_zh="通用文本文件；已记录编码、样本行数和 MIME 类型。",
            summary_en="Generic text file; recorded encoding, sampled line count, and MIME type.",
            structure={
                "extension": path.suffix,
                "mime_type": mime_type,
                "encoding_hint": encoding_hint,
                "detected_encoding": encoding,
                "sample_line_count": len(lines),
                "sample_truncated": truncated,
            },
            warnings=warnings,
        )

    warnings = ["No deep parser matched; only file metadata was recorded."]
    if reason:
        warnings.append(reason)
    return Analysis(
        format_name=mime_type or "Unknown binary",
        analyzer="generic-binary-metadata",
        status="unsupported" if reason is None else "error",
        language="zh",
        summary_zh="二进制或未知格式文件；仅记录基础元数据。",
        summary_en="Binary or unknown-format file; recorded basic metadata only.",
        structure={
            "extension": path.suffix,
            "mime_type": mime_type,
            "encoding_hint": encoding_hint,
        },
        warnings=warnings,
    )


def analyze_file(path: Path) -> Analysis:
    extension = path.suffix.casefold()
    try:
        if extension in {".csv", ".tsv", ".tab"}:
            return analyze_delimited(path, None)
        if extension in {".xlsx", ".xls", ".xlsm", ".ods"}:
            return analyze_excel(path)
        if extension == ".parquet":
            return analyze_parquet(path)
        if extension in {".feather", ".arrow"}:
            return analyze_feather(path)
        if extension == ".json":
            return analyze_json(path, json_lines=False)
        if extension in {".jsonl", ".ndjson"}:
            return analyze_json(path, json_lines=True)
        if extension in {".yaml", ".yml"}:
            return analyze_yaml(path)
        if extension == ".toml":
            return analyze_toml(path)
        if extension in {".rds", ".rda", ".rdata"}:
            return analyze_r_data(path)
        if extension == ".pdf":
            return analyze_pdf(path)
        if extension == ".docx":
            return analyze_docx(path)
        if extension in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".tif",
            ".tiff",
            ".webp",
        }:
            return analyze_image(path)
        if extension in SQLITE_EXTENSIONS:
            return analyze_sqlite(path)
        if extension in ARCHIVE_EXTENSIONS:
            return analyze_zip_archive(path)
        if extension in TAR_EXTENSIONS:
            return analyze_tar_archive(path)
        if extension == ".gz":
            return analyze_gzip_stream(path)
        if extension in XML_EXTENSIONS:
            return analyze_xml(path)
        if extension in HTML_EXTENSIONS:
            return analyze_html(path)
        if extension == ".ipynb":
            return analyze_ipynb(path)
        if extension == ".dta":
            return analyze_stata(path)
        if extension in TEXT_EXTENSIONS or extension in CODE_EXTENSIONS:
            return analyze_code_or_text(path)
        return generic_analysis(path)
    except Exception as error:
        return generic_analysis(
            path,
            reason=f"{type(error).__name__}: {str(error)[:500]}",
        )


def catalog_paths(task_root: Path) -> Tuple[Path, Path, Path]:
    catalog_root = task_root / ".file-catalog"
    return (
        catalog_root,
        catalog_root / "documents",
        catalog_root / "catalog.sqlite3",
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.replace(str(temporary), str(path))


def ensure_catalog(task_root: Path) -> Tuple[Path, Path, Path]:
    catalog_root, documents_root, database_path = catalog_paths(task_root)
    documents_root.mkdir(parents=True, exist_ok=True)
    gitignore = "\n".join(
        [
            "# Machine index and transient files; Markdown explanations remain trackable.",
            "/catalog.sqlite3",
            "/catalog.sqlite3-shm",
            "/catalog.sqlite3-wal",
            "*.tmp",
            "",
        ]
    )
    gitignore_path = catalog_root / ".gitignore"
    if not gitignore_path.exists() or gitignore_path.read_text(
        encoding="utf-8", errors="replace"
    ) != gitignore:
        atomic_write_text(gitignore_path, gitignore)
    return catalog_root, documents_root, database_path


def connect_database(database_path: Path, create: bool) -> Optional[sqlite3.Connection]:
    if not create and not database_path.exists():
        return None
    connection = sqlite3.connect(str(database_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    if create:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                relative_path TEXT PRIMARY KEY,
                source_absolute TEXT NOT NULL,
                task_root_at_scan TEXT NOT NULL,
                document_relative TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                file_type TEXT NOT NULL,
                analyzer TEXT NOT NULL,
                language TEXT NOT NULL,
                status TEXT NOT NULL,
                summary_zh TEXT NOT NULL,
                summary_en TEXT NOT NULL,
                structure_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                search_text TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                reused_from TEXT,
                catalog_version INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)"
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(files)").fetchall()
        }
        if "catalog_version" not in columns:
            connection.execute(
                "ALTER TABLE files ADD COLUMN catalog_version INTEGER NOT NULL DEFAULT 0"
            )
        connection.commit()
    return connection


def analysis_from_row(row: sqlite3.Row) -> Analysis:
    return Analysis(
        format_name=row["file_type"],
        analyzer=row["analyzer"],
        status=row["status"],
        language=row["language"],
        summary_zh=row["summary_zh"],
        summary_en=row["summary_en"],
        structure=json.loads(row["structure_json"]),
        warnings=json.loads(row["warnings_json"]),
    )


def render_document(
    task_root: Path,
    source: Path,
    relative_path: str,
    digest: str,
    analysis: Analysis,
    reused_from: Optional[str],
) -> str:
    stat = source.stat()
    modified = dt.datetime.fromtimestamp(
        stat.st_mtime, tz=dt.timezone.utc
    ).replace(microsecond=0).isoformat()
    analyzed = utc_now()
    title = source.name
    structure_json = json.dumps(
        analysis.structure,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    warning_lines = analysis.warnings or ["None"]

    frontmatter = "\n".join(
        [
            "---",
            f"catalog_version: {CATALOG_VERSION}",
            f"relative_path: {yaml_quote(relative_path)}",
            f"absolute_path_at_scan: {yaml_quote(str(source))}",
            f"task_root_at_scan: {yaml_quote(str(task_root))}",
            f"sha256: {yaml_quote(digest)}",
            f"size_bytes: {stat.st_size}",
            f"modified_utc: {yaml_quote(modified)}",
            f"analyzed_utc: {yaml_quote(analyzed)}",
            f"status: {yaml_quote(analysis.status)}",
            f"format: {yaml_quote(analysis.format_name)}",
            f"analyzer: {yaml_quote(analysis.analyzer)}",
            "---",
        ]
    )

    if analysis.language == "en":
        summary = analysis.summary_en
        warning_block = "\n".join(f"- {item}" for item in warning_lines)
        reuse_line = (
            f"- Reused structure from task-relative path: `{reused_from}`"
            if reused_from
            else "- Structure was parsed from this file."
        )
        body = f"""
# {title}

## Location and freshness

- Task-relative path: `{relative_path}`
- Absolute path at scan: `{source}`
- Size: {stat.st_size} bytes
- Modified: {modified}
- SHA-256: `{digest}`
- Status: `{analysis.status}`
- Format/analyzer: `{analysis.format_name}` / `{analysis.analyzer}`
{reuse_line}

## Content overview

{summary}

## Data or file structure

```json
{structure_json}
```

## Limits and warnings

{warning_block}

This explanation intentionally omits raw rows, cell samples, paragraph excerpts, category values, and source-code snippets.
""".lstrip()
    else:
        summary = analysis.summary_zh
        warning_block = "\n".join(f"- {item}" for item in warning_lines)
        reuse_line = (
            f"- 结构复用来源（任务相对路径）：`{reused_from}`"
            if reused_from
            else "- 结构由当前文件解析得到。"
        )
        body = f"""
# {title}

## 位置与新鲜度

- 任务相对路径：`{relative_path}`
- 扫描时绝对路径：`{source}`
- 大小：{stat.st_size} 字节
- 修改时间：{modified}
- SHA-256：`{digest}`
- 状态：`{analysis.status}`
- 格式/解析器：`{analysis.format_name}` / `{analysis.analyzer}`
{reuse_line}

## 内容概览

{summary}

## 数据或文件结构

```json
{structure_json}
```

## 限制与警告

{warning_block}

本说明有意省略原始数据行、单元格样例、正文段落、类别值和源代码片段。
""".lstrip()
    return frontmatter + "\n\n" + body


def upsert_entry(
    connection: sqlite3.Connection,
    task_root: Path,
    source: Path,
    relative_path: str,
    document_relative: str,
    digest: str,
    analysis: Analysis,
    reused_from: Optional[str],
) -> None:
    stat = source.stat()
    structure_json = json.dumps(analysis.structure, ensure_ascii=False, sort_keys=True)
    warnings_json = json.dumps(analysis.warnings, ensure_ascii=False)
    search_text = "\n".join(
        [
            relative_path,
            source.name,
            analysis.format_name,
            analysis.analyzer,
            analysis.summary_zh,
            analysis.summary_en,
            structure_json,
        ]
    )
    connection.execute(
        """
        INSERT INTO files (
            relative_path, source_absolute, task_root_at_scan, document_relative,
            size_bytes, mtime_ns, sha256, file_type, analyzer, language, status,
            summary_zh, summary_en, structure_json, warnings_json, search_text,
            analyzed_at, reused_from, catalog_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            source_absolute=excluded.source_absolute,
            task_root_at_scan=excluded.task_root_at_scan,
            document_relative=excluded.document_relative,
            size_bytes=excluded.size_bytes,
            mtime_ns=excluded.mtime_ns,
            sha256=excluded.sha256,
            file_type=excluded.file_type,
            analyzer=excluded.analyzer,
            language=excluded.language,
            status=excluded.status,
            summary_zh=excluded.summary_zh,
            summary_en=excluded.summary_en,
            structure_json=excluded.structure_json,
            warnings_json=excluded.warnings_json,
            search_text=excluded.search_text,
            analyzed_at=excluded.analyzed_at,
            reused_from=excluded.reused_from,
            catalog_version=excluded.catalog_version
        """,
        (
            relative_path,
            str(source),
            str(task_root),
            document_relative,
            stat.st_size,
            stat.st_mtime_ns,
            digest,
            analysis.format_name,
            analysis.analyzer,
            analysis.language,
            analysis.status,
            analysis.summary_zh,
            analysis.summary_en,
            structure_json,
            warnings_json,
            search_text,
            utc_now(),
            reused_from,
            CATALOG_VERSION,
        ),
    )


def render_index(connection: sqlite3.Connection, task_root: Path, index_path: Path) -> None:
    rows = connection.execute(
        """
        SELECT relative_path, file_type, status, document_relative, analyzed_at
        FROM files
        ORDER BY lower(relative_path)
        """
    ).fetchall()
    lines = [
        "# File Catalog Index",
        "",
        f"> Task root at generation: `{task_root}`",
        f"> Generated: {utc_now()}",
        "",
        "| Status | Task-relative path | Format | Explanation |",
        "|---|---|---|---|",
    ]
    for row in rows:
        relative = str(row["relative_path"]).replace("|", "\\|")
        file_type = str(row["file_type"]).replace("|", "\\|")
        link = Path(row["document_relative"]).as_posix()
        lines.append(
            f"| {row['status']} | `{relative}` | {file_type} | [document]({link}) |"
        )
    lines.extend(
        [
            "",
            "Use the skill's `search` command instead of loading this entire index into Agent context.",
            "",
        ]
    )
    atomic_write_text(index_path, "\n".join(lines))


def result_record(
    status: str,
    source: Path,
    relative_path: str,
    document: str,
    reused_from: Optional[str] = None,
) -> Dict[str, str]:
    return {
        "status": status,
        "source": str(source),
        "relative_path": relative_path,
        "document": document,
        "reused_from": reused_from or "",
    }


def catalog_files(
    task_root: Path,
    raw_paths: Sequence[str],
    extra_excludes: Sequence[str] = (),
) -> List[Dict[str, str]]:
    catalog_root, documents_root, database_path = ensure_catalog(task_root)
    connection = connect_database(database_path, create=True)
    assert connection is not None
    candidates, issues, full_scan = gather_files(task_root, raw_paths, extra_excludes)
    results = list(issues)
    seen: set[str] = set()

    # 第一阶段：串行确定每个文件的新鲜度，并完成跨运行的内容复用。
    pending: List[Tuple[Path, str, str, Path, str]] = []
    for source in candidates:
        relative_path = relative_key(source, task_root)
        seen.add(relative_path)
        stat = source.stat()
        row = connection.execute(
            "SELECT * FROM files WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        doc_relative = f"documents/{document_id(relative_path)}.md"
        doc_path = catalog_root / doc_relative

        if (
            row is not None
            and row["size_bytes"] == stat.st_size
            and row["mtime_ns"] == stat.st_mtime_ns
            and row["status"] != "missing"
            and row["catalog_version"] == CATALOG_VERSION
            and doc_path.exists()
        ):
            if (
                row["source_absolute"] != str(source)
                or row["task_root_at_scan"] != str(task_root)
            ):
                stored_analysis = analysis_from_row(row)
                relocated_document = render_document(
                    task_root,
                    source,
                    relative_path,
                    row["sha256"],
                    stored_analysis,
                    row["reused_from"],
                )
                atomic_write_text(doc_path, relocated_document)
                upsert_entry(
                    connection,
                    task_root,
                    source,
                    relative_path,
                    doc_relative,
                    row["sha256"],
                    stored_analysis,
                    row["reused_from"],
                )
            results.append(
                result_record(
                    row["status"],
                    source,
                    relative_path,
                    str(doc_path),
                    row["reused_from"],
                )
            )
            continue

        digest = sha256_file(source)
        duplicate = connection.execute(
            """
            SELECT * FROM files
            WHERE sha256 = ?
              AND relative_path <> ?
              AND status IN ('fresh', 'unsupported')
            ORDER BY analyzed_at DESC
            LIMIT 1
            """,
            (digest, relative_path),
        ).fetchone()
        if duplicate is not None:
            reused_from = duplicate["relative_path"]
            analysis = analysis_from_row(duplicate)
            document = render_document(
                task_root,
                source,
                relative_path,
                digest,
                analysis,
                reused_from,
            )
            atomic_write_text(doc_path, document)
            upsert_entry(
                connection,
                task_root,
                source,
                relative_path,
                doc_relative,
                digest,
                analysis,
                reused_from,
            )
            results.append(
                result_record(
                    analysis.status,
                    source,
                    relative_path,
                    str(doc_path),
                    reused_from,
                )
            )
            continue
        pending.append((source, relative_path, doc_relative, doc_path, digest))

    # 第二阶段：同一批内按 SHA-256 分组复用，每组只解析第一个文件；
    # 剩余文件在并行解析后按确定顺序写文档与索引。
    try:
        groups: Dict[str, List[Tuple[Path, str, str, Path]]] = {}
        for source, relative_path, doc_relative, doc_path, digest in pending:
            groups.setdefault(digest, []).append(
                (source, relative_path, doc_relative, doc_path)
            )
        analyzed: Dict[str, Analysis] = {}
        if groups:
            workers = max(1, min(MAX_PARSE_WORKERS, os.cpu_count() or 1))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(analyze_file, entries[0][0]): digest
                    for digest, entries in groups.items()
                }
                for future in futures:
                    analyzed[futures[future]] = future.result()

        for source, relative_path, doc_relative, doc_path, digest in pending:
            group = groups[digest]
            reused_from: Optional[str] = (
                relative_key(group[0][0], task_root)
                if group[0][1] != relative_path
                else None
            )
            analysis = analyzed[digest]
            document = render_document(
                task_root,
                source,
                relative_path,
                digest,
                analysis,
                reused_from,
            )
            atomic_write_text(doc_path, document)
            upsert_entry(
                connection,
                task_root,
                source,
                relative_path,
                doc_relative,
                digest,
                analysis,
                reused_from,
            )
            results.append(
                result_record(
                    analysis.status,
                    source,
                    relative_path,
                    str(doc_path),
                    reused_from,
                )
            )

        if full_scan:
            existing_rows = connection.execute(
                "SELECT relative_path FROM files"
            ).fetchall()
            missing_paths = [
                row["relative_path"]
                for row in existing_rows
                if row["relative_path"] not in seen
            ]
            connection.executemany(
                "UPDATE files SET status = 'missing' WHERE relative_path = ?",
                [(path,) for path in missing_paths],
            )

        connection.commit()
        render_index(connection, task_root, catalog_root / "INDEX.md")
    finally:
        connection.close()
    return results


def lookup_files(
    task_root: Path,
    raw_paths: Sequence[str],
    extra_excludes: Sequence[str] = (),
) -> List[Dict[str, str]]:
    catalog_root, _, database_path = catalog_paths(task_root)
    candidates, issues, _ = gather_files(task_root, raw_paths, extra_excludes)
    connection = connect_database(database_path, create=False)
    results = list(issues)
    if connection is None:
        for source in candidates:
            results.append(
                result_record(
                    "missing",
                    source,
                    relative_key(source, task_root),
                    "",
                )
            )
        return results

    try:
        for source in candidates:
            relative_path = relative_key(source, task_root)
            row = connection.execute(
                "SELECT * FROM files WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
            if row is None:
                results.append(
                    result_record("missing", source, relative_path, "")
                )
                continue
            stat = source.stat()
            document = str(catalog_root / row["document_relative"])
            if (
                row["size_bytes"] == stat.st_size
                and row["mtime_ns"] == stat.st_mtime_ns
                and Path(document).exists()
            ):
                status = row["status"]
            else:
                status = "stale"
            results.append(
                result_record(
                    status,
                    source,
                    relative_path,
                    document,
                    row["reused_from"],
                )
            )
    finally:
        connection.close()
    return results


def search_catalog(
    task_root: Path,
    query: str,
    limit: int,
) -> List[Dict[str, str]]:
    catalog_root, _, database_path = catalog_paths(task_root)
    connection = connect_database(database_path, create=False)
    if connection is None:
        return []
    try:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        rows = connection.execute(
            """
            SELECT relative_path, source_absolute, document_relative, status,
                   file_type, reused_from
            FROM files
            WHERE relative_path LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR source_absolute LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR search_text LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY
                CASE WHEN relative_path LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 0 ELSE 1 END,
                lower(relative_path)
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [
            {
                "status": row["status"],
                "source": row["source_absolute"],
                "relative_path": row["relative_path"],
                "document": str(catalog_root / row["document_relative"]),
                "format": row["file_type"],
                "reused_from": row["reused_from"] or "",
            }
            for row in rows
        ]
    finally:
        connection.close()


def catalog_info(task_root: Path) -> List[Dict[str, Any]]:
    """Summarize the task-local catalog: counts by status and format."""
    catalog_root, _, database_path = catalog_paths(task_root)
    connection = connect_database(database_path, create=False)
    if connection is None:
        return [{"catalog_exists": False}]
    try:
        status_counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, count(*) AS count FROM files GROUP BY status"
            ).fetchall()
        }
        format_counts = {
            row["file_type"]: row["count"]
            for row in connection.execute(
                "SELECT file_type, count(*) AS count FROM files "
                "GROUP BY file_type ORDER BY count(*) DESC LIMIT 20"
            ).fetchall()
        }
        last_analyzed = connection.execute(
            "SELECT max(analyzed_at) AS value FROM files"
        ).fetchone()["value"]
        return [
            {
                "catalog_exists": True,
                "catalog_root": str(catalog_root),
                "database": str(database_path),
                "total_entries": sum(status_counts.values()),
                "status_counts": status_counts,
                "format_counts": format_counts,
                "last_analyzed": last_analyzed,
            }
        ]
    finally:
        connection.close()


def print_results(records: Sequence[Dict[str, Any]], limit: int, json_mode: bool) -> None:
    shown = list(records[:limit])
    if json_mode:
        payload = {
            "results": shown,
            "summary": {
                "total": len(records),
                "shown": len(shown),
                "omitted": max(0, len(records) - len(shown)),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for record in shown:
        parts = [
            f"status={record.get('status', '')}",
            f"path={record.get('relative_path', '')}",
        ]
        if record.get("format"):
            parts.append(f"format={record['format']}")
        if record.get("document"):
            parts.append(f"document={record['document']}")
        if record.get("reused_from"):
            parts.append(f"reused_from={record['reused_from']}")
        if record.get("catalog_exists") is not None and "path" not in record:
            parts = [f"{key}={value}" for key, value in record.items()]
        print(" | ".join(parts))
    if len(records) > len(shown):
        print(f"... {len(records) - len(shown)} additional result(s) omitted by --limit")
    print(f"summary total={len(records)} shown={len(shown)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and reuse per-task structural explanations for local files."
    )

    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of status lines.",
    )
    scan_parent = argparse.ArgumentParser(add_help=False, parents=[json_parent])
    scan_parent.add_argument(
        "--exclude",
        default="",
        help="Comma-separated file/directory names to skip during scans.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser(
        "catalog", help="Catalog missing or stale task files.", parents=[scan_parent]
    )
    catalog_parser.add_argument(
        "--task-root", help="Large-task root; defaults to the current directory."
    )
    catalog_parser.add_argument(
        "--limit", type=int, default=30, help="Maximum result rows printed."
    )
    catalog_parser.add_argument(
        "paths",
        nargs="*",
        help="In-root files/directories; omit to recursively catalog the task root.",
    )

    lookup_parser = subparsers.add_parser(
        "lookup", help="Check whether explanations are fresh.", parents=[scan_parent]
    )
    lookup_parser.add_argument(
        "--task-root", help="Large-task root; defaults to the current directory."
    )
    lookup_parser.add_argument(
        "--limit", type=int, default=30, help="Maximum result rows printed."
    )
    lookup_parser.add_argument(
        "paths", nargs="+", help="In-root files or directories to check."
    )

    search_parser = subparsers.add_parser(
        "search", help="Search the current task's catalog.", parents=[json_parent]
    )
    search_parser.add_argument(
        "--task-root", help="Large-task root; defaults to the current directory."
    )
    search_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum search results."
    )
    search_parser.add_argument("query", help="Path, name, format, field, or keyword.")

    info_parser = subparsers.add_parser(
        "info", help="Summarize the task-local catalog.", parents=[json_parent]
    )
    info_parser.add_argument(
        "--task-root", help="Large-task root; defaults to the current directory."
    )
    info_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum listed formats."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Windows pipes inherit a locale-dependent encoding (often cp1252). Force
    # UTF-8 so paths and structural names remain portable across all terminals.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        task_root = resolve_task_root(args.task_root)
        limit = max(1, min(int(args.limit), 500))
        excludes = [name for name in getattr(args, "exclude", "").split(",") if name.strip()]
        if args.command == "catalog":
            records = catalog_files(task_root, args.paths, excludes)
        elif args.command == "lookup":
            records = lookup_files(task_root, args.paths, excludes)
        elif args.command == "search":
            records = search_catalog(task_root, args.query, limit)
        else:
            records = catalog_info(task_root)
        print_results(records, limit, json_mode=args.json)
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error={type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
