#!/usr/bin/env bash
# Install the agent-sandbox CLI and its skill WITHOUT the Claude Code plugin system
# (other agents, or if you just want the CLI). Re-run any time; it is idempotent.
#
#   ./scripts/install.sh                      # CLI + skill in ~/.claude/skills + end-of-session hooks
#   ./scripts/install.sh --skills-dir DIR     # put the skill in another agent's skills directory
#   ./scripts/install.sh --no-hooks           # skip the Claude Code SessionStart/SessionEnd hooks
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="${HOME}/.claude/skills"
HOOKS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --skills-dir) SKILLS_DIR="$2"; shift 2 ;;
    --no-hooks) HOOKS=0; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

echo "==> Installing the agent-sandbox CLI (editable: 'git pull' updates it)"
uv tool install --force --editable "$REPO"

echo "==> Linking the skill into $SKILLS_DIR"
mkdir -p "$SKILLS_DIR"
ln -sfn "$REPO/.agents/skills/agent-platform-sandbox-cmek" "$SKILLS_DIR/agent-platform-sandbox-cmek"

if [ "$HOOKS" = 1 ]; then
  echo "==> Installing Claude Code hooks (stop sandboxes when a session ends)"
  agent-sandbox hooks install --scope user --format text
fi

echo
echo "Done. Next: agent-sandbox config show   (then see README: first-time setup)"
agent-sandbox --version
