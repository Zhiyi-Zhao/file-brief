#!/usr/bin/env bash
# =============================================================================
# install.sh — Install the file-brief skill into one or more agent
# skill homes (OpenAI Codex, Claude Code, DeepSeek Harness, shared ~/.agents).
#
# Usage (from the repository root):
#   ./install.sh            # all homes
#   ./install.sh codex      # one target
#   ./install.sh dsh agents # multiple targets
#
# Targets: codex | claude | dsh | agents | all
# =============================================================================
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$repository_root/skills/file-brief"

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "error: skill source not found at $source_dir" >&2
  echo "run this script from the repository root" >&2
  exit 1
fi

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]] || [[ "${targets[0]}" == "all" ]]; then
  targets=(codex claude dsh agents)
fi

codex_home="${CODEX_HOME:-$HOME/.codex}"
claude_home="$HOME/.claude"
dsh_home="${DSH_HOME:-$HOME/.dsh}"
agents_home="${DSH_AGENTS_HOME:-$HOME/.agents}"

install_skill() {
  local destination_root="$1"
  local label="$2"
  local destination="$destination_root/file-brief"
  mkdir -p "$destination_root"
  rm -rf "$destination"
  cp -R "$source_dir" "$destination"
  echo "installed -> $destination ($label)"
}

for target in "${targets[@]}"; do
  case "$target" in
    codex)  install_skill "$codex_home/skills" "OpenAI Codex" ;;
    claude) install_skill "$claude_home/skills" "Claude Code" ;;
    dsh)    install_skill "$dsh_home/skills" "DeepSeek Harness" ;;
    agents) install_skill "$agents_home/skills" "shared ~/.agents" ;;
    *)
      echo "error: unknown target '$target' (codex | claude | dsh | agents | all)" >&2
      exit 1
      ;;
  esac
done

echo ""
echo "Done. Start a new agent session so the skill list reloads."
