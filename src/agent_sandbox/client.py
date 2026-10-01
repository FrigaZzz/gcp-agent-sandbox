import os
from pathlib import Path
from typing import List, Optional

# pyrefly: ignore [missing-import]
import agentplatform

REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
PROJECT_ENV_NAME = ".agent-sandbox.env"
CONFIG_KEYS = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "KMS_KEY_NAME",
    "AGENT_RUNTIME_NAME",
    "SANDBOX_TEMPLATE_NAME",
]


def user_env_path() -> Path:
    """Per-user config file, used when the CLI is installed (e.g. as a Claude Code plugin)."""
    return Path.home() / ".config" / "agent-sandbox" / "env"


def project_root(start: Optional[Path] = None) -> Path:
    """Nearest ancestor containing ``.git`` (else the starting directory)."""
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        if (directory / ".git").exists():
            return directory
    return here


def find_project_env(start: Optional[Path] = None) -> Optional[Path]:
    """Nearest ``.agent-sandbox.env`` from ``start`` (default: cwd) upwards, never above ``$HOME``.

    This is how a repository pins *its own* sandbox runtime/template (project-scoped sandboxes).
    The file name is specific to this tool on purpose; a generic ``.env`` is never read.
    """
    here = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    for directory in [here, *here.parents]:
        candidate = directory / PROJECT_ENV_NAME
        if candidate.is_file():
            return candidate
        if directory == home:
            break
    return None


def env_search_paths() -> List[Path]:
    """Candidate env files, highest priority first.

    1. ``$AGENT_SANDBOX_ENV`` (explicit override)
    2. ``.agent-sandbox.env`` found walking up from the current directory (per-project sandbox)
    3. ``~/.config/agent-sandbox/env`` (per-user config; see ``agent-sandbox config``)
    4. ``.env`` at the repository root (source checkouts of this tool)

    A plain ``./.env`` is deliberately **not** read: an AI agent works in arbitrary projects, and
    another project's ``.env`` must never decide which GCP project sandboxes are created in.
    The real environment always wins over every file.
    """
    paths: List[Path] = []
    explicit = os.environ.get("AGENT_SANDBOX_ENV")
    if explicit:
        paths.append(Path(explicit).expanduser())
    project = find_project_env()
    if project:
        paths.append(project)
    paths.append(user_env_path())
    paths.append(REPO_ROOT_ENV)
    return paths


def _load_env_file(env_file: Path) -> None:
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = os.path.expandvars(v.strip("\"' "))
            if k not in os.environ:
                os.environ[k] = v


def load_environment(env_file_path: Optional[Path] = None) -> None:
    """Load variables from an env file (never overriding the real environment).

    With an explicit path only that file is read; otherwise every existing file
    from :func:`env_search_paths` is read in priority order.
    """
    if env_file_path is not None:
        if env_file_path.exists():
            _load_env_file(env_file_path)
        return
    for candidate in env_search_paths():
        if candidate.exists():
            _load_env_file(candidate)


def default_env_path(scope: Optional[str] = None) -> Path:
    """File that :func:`persist_env_var` writes to when no path is given.

    ``scope="project"`` -> ``<git root>/.agent-sandbox.env``; ``scope="user"`` -> the per-user file.
    Without a scope: ``$AGENT_SANDBOX_ENV`` (even if it does not exist yet), else the
    highest-priority existing file, else the per-user file.
    """
    if scope == "project":
        return find_project_env() or project_root() / PROJECT_ENV_NAME
    if scope == "user":
        return user_env_path()
    explicit = os.environ.get("AGENT_SANDBOX_ENV")
    if explicit:
        return Path(explicit).expanduser()
    for candidate in env_search_paths():
        if candidate.exists():
            return candidate
    return user_env_path()


def persist_env_var(key: str, value: str, env_path: Optional[Path] = None) -> Path:
    """Set ``export KEY="value"`` in an env file, replacing any (commented) existing line."""
    path = env_path or default_env_path()
    new_line = f'export {key}="{value}"'
    lines: List[str] = []
    found = False
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip().startswith((f"export {key}=", f"# export {key}=")):
                if not found:
                    lines.append(new_line)
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    os.environ[key] = value
    return path


def get_agent_client(
    project: Optional[str] = None,
    location: Optional[str] = None,
    api_version: str = "v1beta1",
) -> agentplatform.Client:
    """Return an initialized Agent Platform client."""
    load_environment()

    proj = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    loc = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west8")

    if not proj:
        raise ValueError(
            "Project ID not found. Ensure GOOGLE_CLOUD_PROJECT is set or configured in .env."
        )

    return agentplatform.Client(
        project=proj,
        location=loc,
        http_options={"api_version": api_version},
    )


def get_runtime_name() -> str:
    """Retrieve the parent Agent Runtime resource name from the environment."""
    load_environment()
    runtime_name = os.environ.get("AGENT_RUNTIME_NAME")
    if not runtime_name:
        raise ValueError(
            "AGENT_RUNTIME_NAME not found in environment. "
            "Run `agent-sandbox runtime create` (or scripts/create_instance.py) or configure .env."
        )
    return runtime_name
