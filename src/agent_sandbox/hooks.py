"""Claude Code hook definitions that stop billed sandboxes when a session ends.

Two hooks are provided:

* ``SessionEnd``  - runs ``agent-sandbox cleanup`` so no sandbox outlives the chat.
  Claude Code gives SessionEnd hooks a 1.5s budget unless the hook sets ``timeout``
  (honoured up to 60s), so the entry sets one.
* ``SessionStart`` - runs ``agent-sandbox status --brief``; its stdout is added to
  Claude's context, so leftovers from a crashed session are noticed immediately.

The server-side TTL set at creation time remains the final backstop.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

MARKER = "agent-sandbox"

HOOKS: Dict[str, Dict[str, Any]] = {
    "SessionEnd": {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": "agent-sandbox cleanup --quiet || true",
                "timeout": 60,
            }
        ],
    },
    "SessionStart": {
        "matcher": "startup|resume|clear",
        "hooks": [
            {
                "type": "command",
                "command": "agent-sandbox status --brief || true",
                "timeout": 30,
            }
        ],
    },
}


def snippet() -> Dict[str, Any]:
    """The ``hooks`` block to merge into a Claude Code settings file."""
    return {"hooks": {event: [entry] for event, entry in HOOKS.items()}}


def settings_path(scope: str, project_dir: Optional[Path] = None) -> Path:
    base = project_dir or Path.cwd()
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        return base / ".claude" / "settings.json"
    if scope == "local":
        return base / ".claude" / "settings.local.json"
    raise ValueError(f"Unknown scope {scope!r}; use user, project or local")


def _is_ours(group: Dict[str, Any]) -> bool:
    return any(MARKER in hook.get("command", "") for hook in group.get("hooks", []))


def merge_hooks(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``settings`` with our hooks added once (replacing older copies of them)."""
    merged = dict(settings)
    hooks = dict(merged.get("hooks", {}))
    for event, entry in HOOKS.items():
        groups = [g for g in hooks.get(event, []) if not _is_ours(g)]
        groups.append(entry)
        hooks[event] = groups
    merged["hooks"] = hooks
    return merged


def remove_hooks(settings: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(settings)
    hooks = dict(merged.get("hooks", {}))
    for event in list(hooks):
        remaining = [g for g in hooks[event] if not _is_ours(g)]
        if remaining:
            hooks[event] = remaining
        else:
            del hooks[event]
    if hooks:
        merged["hooks"] = hooks
    else:
        merged.pop("hooks", None)
    return merged


def install(scope: str, project_dir: Optional[Path] = None, uninstall: bool = False) -> Path:
    """Merge (or remove) the hooks in the chosen settings file, preserving everything else."""
    path = settings_path(scope, project_dir)
    settings: Dict[str, Any] = {}
    if path.exists():
        text = path.read_text().strip()
        if text:
            settings = json.loads(text)  # raises on invalid JSON rather than clobbering it
    updated = remove_hooks(settings) if uninstall else merge_hooks(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2) + "\n")
    return path
