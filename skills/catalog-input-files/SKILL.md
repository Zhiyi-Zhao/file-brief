---
name: catalog-input-files
description: Catalog local task-folder input files into reusable Markdown explanations and a searchable per-task index. Use whenever a task involves reading, inspecting, analyzing, transforming, or writing code against one or more local files or directories in any language or format — delimited tables (CSV/TSV with delimiter auto-detection), Excel, Parquet/Feather, JSON/JSONL/YAML/TOML, SQLite databases, ZIP/TAR/GZIP archives, XML/HTML documents, Jupyter notebooks, Stata datasets, RDS/RData, source code in common languages, text, Markdown, PDF, DOCX, images, or unknown binary files. Check the task-local catalog before probing source files, and refresh only missing or stale entries.
---

# Catalog Input Files

Create and reuse structural explanations under the current large task folder. The skill is agent-platform agnostic: it works with any agent (OpenAI Codex, Claude Code, DeepSeek Harness, or any CLI-capable agent) that can run the Python entrypoint. Keep the skill code in the agent skill home, but never place generated catalogs under any agent home — they belong to the task folder.

## Workflow

1. Determine the large task root explicitly from the user's task or workspace. Use the current working directory only when no more specific root is available. Do not infer a Git root.
2. Identify the local input files or directories involved in the task. When the task spans many formats or languages, catalog the whole task root once and reuse the explanations.
3. Run `lookup` before writing ad-hoc inspection code:

```bash
python "<skill-dir>/scripts/file_catalog.py" lookup --task-root "<task-root>" "<input-path>"
```

4. Read the returned Markdown explanation when the status is `fresh`, `unsupported`, or `error`. Treat `unsupported` and `error` documents as useful metadata with recorded limitations.
5. Run `catalog` for `missing` or `stale` entries. Omit paths to catalog the entire task recursively:

```bash
python "<skill-dir>/scripts/file_catalog.py" catalog --task-root "<task-root>" "<input-path>"
python "<skill-dir>/scripts/file_catalog.py" catalog --task-root "<task-root>"
```

6. Read only the generated explanation documents needed for the current task. Do not load the entire SQLite database or all source files into context.
7. Use `search` for cross-folder discovery inside the same large task:

```bash
python "<skill-dir>/scripts/file_catalog.py" search --task-root "<task-root>" "column-or-file-name"
```

8. Use `info` to check what is already cataloged before scanning:

```bash
python "<skill-dir>/scripts/file_catalog.py" info --task-root "<task-root>"
```

9. Inspect a raw source file only when the catalog explicitly lacks information required by the task. Do not copy one-off probing logic into the task's production scripts.

For deterministic machine-readable results, add `--json` to any command. To skip specific names during scans, add `--exclude "name1,name2"` to `catalog` or `lookup`.

## Catalog Location and Portability

Write generated content only under `<task-root>\.file-catalog`:

- `INDEX.md`: compact, human-readable file list.
- `documents\<relative-path-hash>.md`: one current explanation per task-relative path.
- `catalog.sqlite3`: searchable machine index.
- `.gitignore`: ignore SQLite, lock, and temporary files while leaving Markdown trackable.

Store both the task-relative path and the absolute path at scan time. Use the task-relative path as the stable identity so the task folder can move without losing matches.

## Interpretation Rules

- Trust a `fresh` explanation instead of re-reading the source merely to rediscover its shape.
- Refresh `stale` entries before relying on them.
- Preserve structural names such as columns, keys, sheet names, headings, functions, classes, tables, and object names.
- Do not store raw rows, cell samples, paragraph excerpts, top categorical values, or source-code snippets.
- Treat statistics as sample-based unless the document explicitly says they are exact.
- Keep unknown or unavailable formats useful through generic metadata and explicit warnings.
- Follow the input language for generated explanations; use Chinese when language cannot be inferred.

## Command Behavior

- `catalog --task-root ROOT [PATH...]`: recursively catalog the whole task when no paths are supplied; otherwise update only the specified in-root files/directories.
- `lookup --task-root ROOT PATH...`: report `fresh`, `stale`, `missing`, `unsupported`, or `error` without dumping source content.
- `search --task-root ROOT QUERY`: search paths, names, formats, summaries, fields, and structural identifiers.
- `info --task-root ROOT`: summarize cataloged entries by status and format.
- Add `--limit N` to cap printed result rows. Defaults are deliberately small to protect context.
- Add `--json` for machine-readable output; add `--exclude "a,b"` to skip names during scans.

## Platform Installation

- OpenAI Codex: copy this folder to `<CODEX_HOME or ~/.codex>/skills/catalog-input-files`.
- Claude Code: copy this folder to `~/.claude/skills/catalog-input-files`.
- DeepSeek Harness: copy this folder to `~/.dsh/skills/catalog-input-files` or `~/.agents/skills/catalog-input-files` (project roots `.dsh/skills` and `.agents/skills` also work).
- Or run the repository's `install.ps1` / `install.sh` which installs to all configured agent homes.

The Python entrypoint resolves its companion `inspect_r_data.R` automatically. It checks `R_SCRIPT_EXE`, then `Rscript` on `PATH`, then common Windows R installation directories.
