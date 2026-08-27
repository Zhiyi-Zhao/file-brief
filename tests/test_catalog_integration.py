"""
===============================================================================
代码介绍 / Test Module Overview
===============================================================================
输入 / Inputs:
  - pytest 提供的临时目录；
  - 仓库中的 skills/file-brief 技能源码；
  - 可选的 Rscript + jsonlite，用于 RDS/RData 测试。

输出 / Outputs:
  - pytest 断言结果；
  - 仅在 pytest 临时目录中生成测试文件和 .file-catalog。

作用 / Purpose:
  端到端验证文件建档、隐私保护、重复复用、增量刷新、跨目录搜索、任务迁移、
  R 数据结构解析和公开仓库卫生。测试不会读取用户文件。

设计逻辑 / Design:
  - 动态生成小型、多格式夹具，不把二进制样例提交到仓库。
  - 通过真实 CLI 调用技能，而不是绕过命令行直接测试内部函数。
  - 用唯一敏感字符串证明说明文档和 SQLite 不保存原始数据值。
  - R 不可用时只跳过 R 专项测试；GitHub Actions 的 R job 会强制执行它。

主要函数 / Main functions:
  run_cli()                 运行 CLI 并限制输出。
  make_common_fixtures()    生成表格、文档、媒体和结构化文本。
  test_catalog_end_to_end() 验证核心工作流。
  test_r_data_inspection()  验证 RDS/RData。
  test_publication_hygiene()验证 README、许可和隐私清理。

调用 / Usage:
  python -m pytest -q
  python -m pytest -q -k "not r_data"
  python -m pytest -q -k "r_data"
===============================================================================
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import pytest
from PIL import Image
from pypdf import PdfWriter


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "file-brief"
CATALOG_SCRIPT = SKILL_ROOT / "scripts" / "file_catalog.py"
R_HELPER = SKILL_ROOT / "scripts" / "inspect_r_data.R"
SECRET = "PRIVATE_" + "UNIQUE_VALUE_" + "94817"
NEW_SECRET = "PRIVATE_" + "NEW_FORMATS_" + "7702"
R_SECRET = "PRIVATE_" + "R_VALUE"


def run_cli(*arguments: str, timeout: int = 240) -> str:
    completed = subprocess.run(
        [sys.executable, str(CATALOG_SCRIPT), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, (
        f"CLI failed with {completed.returncode}\n"
        f"stdout:\n{completed.stdout[-4000:]}\n"
        f"stderr:\n{completed.stderr[-4000:]}"
    )
    return completed.stdout[:20000]


def close_connection(connection: sqlite3.Connection) -> None:
    try:
        connection.close()
    except sqlite3.Error:
        pass


def make_common_fixtures(root: Path) -> list[Path]:
    data_dir = root / "数据 with spaces"
    other_dir = root / "other"
    data_dir.mkdir(parents=True)
    other_dir.mkdir()

    frame = pd.DataFrame(
        {
            "species": ["Quercus robur", SECRET, None],
            "abundance": [3, 7, 1],
            "采样日期": ["2026-01-01", "2026-01-02", "2026-01-03"],
        }
    )
    csv_path = data_dir / "table.csv"
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    duplicate_csv = other_dir / "table-copy.csv"
    shutil.copy2(csv_path, duplicate_csv)
    excel_path = data_dir / "workbook.xlsx"
    frame.to_excel(excel_path, index=False)
    parquet_path = data_dir / "table.parquet"
    frame.drop(columns=["species"]).to_parquet(parquet_path, index=False)

    json_path = data_dir / "nested.json"
    json_path.write_text(
        json.dumps(
            {
                "study": {
                    "site": {"latitude": 55.6, "longitude": 12.5},
                    "measurements": [{"value": 1.2, "unit": "cm"}],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    yaml_path = root / "config.yaml"
    yaml_path.write_text(
        "project:\n  title: 测试项目\n  stages:\n    - ingest\n    - analyse\n",
        encoding="utf-8",
    )
    toml_path = root / "config.toml"
    toml_path.write_text(
        '[project]\nname = "catalog-test"\n[project.paths]\ninput = "data"\n',
        encoding="utf-8",
    )
    python_path = root / "analysis.py"
    python_path.write_text(
        '"""Fixture module."""\nimport json\n\n'
        "class Analyzer:\n    pass\n\n"
        "def load_data(path):\n    return json.loads(path.read_text())\n",
        encoding="utf-8",
    )
    markdown_path = root / "notes.md"
    markdown_path.write_text(
        "# 研究说明\n\n## 输入数据\n\n本文件只用于结构测试。\n",
        encoding="utf-8",
    )

    pdf_path = root / "document.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    image_path = root / "image.png"
    Image.new("RGB", (16, 12), color=(20, 40, 60)).save(image_path)
    binary_path = root / "unknown.bin"
    binary_path.write_bytes(bytes(range(64)) + b"\x00\xff")

    docx_path = root / "document.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>标题</w:t></w:r></w:p>
    <w:p><w:r><w:t>正文内容不应写入说明。</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>单元格</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/media/image1.bin", b"media")

    sqlite_path = root / "database.db"
    connection = sqlite3.connect(sqlite_path)
    try:
        connection.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY, label TEXT, value REAL)"
        )
        connection.execute(
            "INSERT INTO measurements VALUES (1, ?, 2.5)", (NEW_SECRET,)
        )
        connection.execute(
            "CREATE VIEW measurement_view AS SELECT id FROM measurements"
        )
        connection.commit()
    finally:
        connection.close()

    zip_path = root / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("data/inner.txt", f"content {NEW_SECRET}")

    xml_path = root / "structure.xml"
    xml_path.write_text(
        '<?xml version="1.0"?><root xmlns:a="urn:x"><item id="1"/><item id="2"/></root>',
        encoding="utf-8",
    )

    html_path = root / "site.html"
    html_path.write_text(
        f"<html><head><title>T</title></head><body><h1>Hi</h1><p>{NEW_SECRET}</p>"
        "<table><tr><td>1</td></tr></table></body></html>",
        encoding="utf-8",
    )

    notebook_path = root / "notebook.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": 1,
                        "source": [f"print('{NEW_SECRET}')"],
                    },
                    {"cell_type": "markdown", "source": ["# doc"]},
                ],
                "metadata": {"kernelspec": {"name": "python3", "language": "python"}},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )

    semi_path = root / "semi.csv"
    semi_path.write_text("a;b;c\n1;x;true\n2;y;false\n", encoding="utf-8")

    go_path = root / "server.go"
    go_path.write_text(
        'package main\nimport "fmt"\n'
        "func main() { fmt.Println(1) }\ntype Server struct{}\n",
        encoding="utf-8",
    )

    dta_path = root / "dataset.dta"
    frame[["abundance"]].to_stata(dta_path, write_index=False)

    return [
        csv_path,
        duplicate_csv,
        excel_path,
        parquet_path,
        json_path,
        yaml_path,
        toml_path,
        python_path,
        markdown_path,
        pdf_path,
        image_path,
        binary_path,
        docx_path,
        sqlite_path,
        zip_path,
        xml_path,
        html_path,
        notebook_path,
        semi_path,
        go_path,
        dta_path,
    ]


def read_documents(catalog_root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((catalog_root / "documents").glob("*.md"))
    )


def test_catalog_end_to_end(tmp_path: Path) -> None:
    task_root = tmp_path / "原始 task with spaces"
    task_root.mkdir()
    source_files = make_common_fixtures(task_root)

    first_output = run_cli(
        "catalog", "--task-root", str(task_root), "--limit", "100"
    )
    catalog_root = task_root / ".file-catalog"
    database_path = catalog_root / "catalog.sqlite3"
    documents = sorted((catalog_root / "documents").glob("*.md"))

    assert "summary total=" in first_output
    assert (catalog_root / "INDEX.md").exists()
    assert (catalog_root / ".gitignore").exists()
    assert database_path.exists()
    assert len(documents) == len(source_files)

    combined_documents = read_documents(catalog_root)
    assert SECRET not in combined_documents
    assert SECRET.encode("utf-8") not in database_path.read_bytes()
    assert NEW_SECRET not in combined_documents
    assert NEW_SECRET.encode("utf-8") not in database_path.read_bytes()

    connection = sqlite3.connect(database_path)
    try:
        reused_count = connection.execute(
            "SELECT count(*) FROM files WHERE reused_from IS NOT NULL"
        ).fetchone()[0]
        analyzers = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT analyzer FROM files"
            ).fetchall()
        }
        semi_structure = connection.execute(
            "SELECT structure_json FROM files WHERE relative_path = 'semi.csv'"
        ).fetchone()[0]
    finally:
        close_connection(connection)
    assert reused_count >= 1
    assert "pandas-excel" in analyzers
    assert "pyarrow-parquet-metadata" in analyzers
    assert "pypdf-metadata" in analyzers
    assert "docx-zip-xml" in analyzers
    assert "pillow-metadata" in analyzers
    assert "sqlite-schema" in analyzers
    assert "zipfile-central-directory" in analyzers
    assert "xml-iterparse-structure" in analyzers
    assert "htmlparser-structure" in analyzers
    assert "ipynb-structure" in analyzers
    assert "pandas-stata" in analyzers
    assert "generic-source-structure" in analyzers
    assert '"delimiter": ";"' in semi_structure

    document_mtimes = {path.name: path.stat().st_mtime_ns for path in documents}
    run_cli("catalog", "--task-root", str(task_root), "--limit", "100")
    second_mtimes = {
        path.name: path.stat().st_mtime_ns
        for path in (catalog_root / "documents").glob("*.md")
    }
    assert second_mtimes == document_mtimes

    csv_path = task_root / "数据 with spaces" / "table.csv"
    lookup_output = run_cli(
        "lookup", "--task-root", str(task_root), str(csv_path)
    )
    assert "status=fresh" in lookup_output
    search_output = run_cli(
        "search", "--task-root", str(task_root), "species"
    )
    assert "summary total=3" in search_output

    json_path = task_root / "数据 with spaces" / "nested.json"
    json_path.write_text(
        '{"study":{"site":{"latitude":55.7},"new_field":true}}',
        encoding="utf-8",
    )
    future = time.time() + 2
    os.utime(json_path, (future, future))
    stale_output = run_cli(
        "lookup", "--task-root", str(task_root), str(json_path)
    )
    assert "status=stale" in stale_output
    run_cli("catalog", "--task-root", str(task_root), str(json_path))
    assert "status=fresh" in run_cli(
        "lookup", "--task-root", str(task_root), str(json_path)
    )

    moved_root = tmp_path / "移动后 task with spaces"
    shutil.move(str(task_root), str(moved_root))
    moved_csv = moved_root / "数据 with spaces" / "table.csv"
    assert "status=fresh" in run_cli(
        "lookup", "--task-root", str(moved_root), str(moved_csv)
    )
    run_cli("catalog", "--task-root", str(moved_root), "--limit", "100")
    moved_database = moved_root / ".file-catalog" / "catalog.sqlite3"
    connection = sqlite3.connect(moved_database)
    try:
        roots = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT task_root_at_scan FROM files"
            ).fetchall()
        }
    finally:
        close_connection(connection)
    assert roots == {str(moved_root)}
    assert "移动后 task with spaces" in read_documents(
        moved_root / ".file-catalog"
    )


def available_rscript() -> Optional[str]:
    environment_value = os.environ.get("R_SCRIPT_EXE")
    if environment_value and Path(environment_value).is_file():
        return environment_value
    return shutil.which("Rscript")


def test_r_data_inspection(tmp_path: Path) -> None:
    rscript = available_rscript()
    if not rscript:
        pytest.skip("Rscript is not available.")
    dependency_check = subprocess.run(
        [
            rscript,
            "--vanilla",
            "-e",
            "quit(status=ifelse(requireNamespace('jsonlite', quietly=TRUE),0,1))",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if dependency_check.returncode != 0:
        pytest.skip("R package jsonlite is not available.")

    task_root = tmp_path / "r-task"
    task_root.mkdir()
    rds_path = task_root / "data.rds"
    rdata_path = task_root / "data.RData"
    r_command = (
        "df <- data.frame(id=1:3, score=c(1.1, NA, 3.4)); "
        f"meta <- list(name={json.dumps(R_SECRET)}, dims=c(3,2)); "
        f"saveRDS(df, file={json.dumps(rds_path.as_posix())}); "
        f"save(df, meta, file={json.dumps(rdata_path.as_posix())})"
    )
    created = subprocess.run(
        [rscript, "--vanilla", "-e", r_command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    assert created.returncode == 0, created.stderr.decode(
        "utf-8", errors="replace"
    )[-2000:]

    environment = os.environ.copy()
    environment["R_SCRIPT_EXE"] = rscript
    completed = subprocess.run(
        [
            sys.executable,
            str(CATALOG_SCRIPT),
            "catalog",
            "--task-root",
            str(task_root),
            "--limit",
            "20",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]

    database_path = task_root / ".file-catalog" / "catalog.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        deep_count = connection.execute(
            "SELECT count(*) FROM files WHERE analyzer='r-structure-helper'"
        ).fetchone()[0]
    finally:
        close_connection(connection)
    assert deep_count == 2
    assert R_SECRET not in read_documents(
        task_root / ".file-catalog"
    )
    assert R_SECRET.encode("utf-8") not in database_path.read_bytes()


def text_files(root: Path) -> Iterable[Path]:
    allowed = {
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".py",
        ".R",
        ".gitignore",
        "",
    }
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name == "LICENSE" or path.suffix in allowed:
            yield path


def test_publication_hygiene() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in text_files(REPOSITORY_ROOT)
    )
    forbidden = [
        "D:" + "\\zzy",
        "\\" + "Users" + "\\zzy",
        "/" + "Users" + "/zzy",
        "{{" + "GITHUB_OWNER" + "}}",
    ]
    for value in forbidden:
        assert value not in combined

    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    required_readme_terms = [
        "## 中文",
        "## English",
        "为什么发布这个仓库",
        "Why this repository exists",
        "lookup",
        "catalog",
        "search",
        "fresh",
        "stale",
        "missing",
        "unsupported",
        "error",
        ".file-catalog",
    ]
    for term in required_readme_terms:
        assert term in readme

    skill_frontmatter = (SKILL_ROOT / "SKILL.md").read_text(
        encoding="utf-8"
    ).split("---", 2)[1]
    assert "name:" in skill_frontmatter
    assert "description:" in skill_frontmatter
    assert "metadata:" not in skill_frontmatter
    assert "file-brief" in skill_frontmatter


def test_json_and_info_outputs(tmp_path: Path) -> None:
    task_root = tmp_path / "json-task"
    task_root.mkdir()
    (task_root / "plain.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    run_cli("catalog", "--task-root", str(task_root), "--limit", "10")

    output = run_cli(
        "lookup", "--task-root", str(task_root), "plain.csv", "--json"
    )
    payload = json.loads(output)
    assert payload["results"][0]["status"] == "fresh"
    assert payload["results"][0]["relative_path"] == "plain.csv"
    assert payload["summary"]["total"] == 1

    info_output = run_cli("info", "--task-root", str(task_root), "--json")
    info = json.loads(info_output)
    assert info["results"][0]["catalog_exists"] is True
    assert info["results"][0]["status_counts"]["fresh"] == 1
    assert info["results"][0]["format_counts"]["CSV"] == 1

    search_output = run_cli(
        "search", "--task-root", str(task_root), "plain", "--json"
    )
    search_payload = json.loads(search_output)
    assert search_payload["results"][0]["format"] == "CSV"


def test_exclude_flag(tmp_path: Path) -> None:
    task_root = tmp_path / "exclude-task"
    task_root.mkdir()
    (task_root / "keep.csv").write_text("x\n1\n", encoding="utf-8")
    (task_root / "skip.csv").write_text("x\n2\n", encoding="utf-8")
    output = run_cli(
        "catalog",
        "--task-root",
        str(task_root),
        "--exclude",
        "skip.csv",
        "--limit",
        "10",
    )
    assert "skip.csv" not in output
    assert "keep.csv" in output
    assert "summary total=1" in output


def test_catalog_version_migration(tmp_path: Path) -> None:
    """A v1 database without catalog_version is migrated and re-analyzed."""
    task_root = tmp_path / "migration-task"
    task_root.mkdir()
    (task_root / "plain.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    run_cli("catalog", "--task-root", str(task_root), "--limit", "10")

    database_path = task_root / ".file-catalog" / "catalog.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("ALTER TABLE files DROP COLUMN catalog_version")
        connection.commit()
    finally:
        close_connection(connection)

    run_cli("catalog", "--task-root", str(task_root), "--limit", "10")
    connection = sqlite3.connect(database_path)
    try:
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT catalog_version FROM files"
            ).fetchall()
        }
    finally:
        close_connection(connection)
    assert versions == {2}


def test_archive_privacy_and_structure(tmp_path: Path) -> None:
    """ZIP/TAR members are listed by name only; content never lands in docs."""
    task_root = tmp_path / "archive-task"
    task_root.mkdir()
    zip_path = task_root / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("data/inner.txt", f"member content {NEW_SECRET}")
    run_cli("catalog", "--task-root", str(task_root), "--limit", "10")
    combined = read_documents(task_root / ".file-catalog")
    assert "inner.txt" in combined
    assert NEW_SECRET not in combined
