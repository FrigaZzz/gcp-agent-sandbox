import os
import sys
from pathlib import Path
import pytest

# Ensure src/ is importable
repo_root = Path(__file__).resolve().parents[1]
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agent_sandbox.client import get_agent_client, get_runtime_name


@pytest.fixture(autouse=True)
def isolated_config(request, tmp_path_factory, monkeypatch):
    """Offline tests must never read or WRITE the developer's real config (~/.config, repo .env).

    Live e2e tests (marker ``e2e``) are the only ones allowed to see the real configuration.
    """
    if request.node.get_closest_marker("e2e"):
        yield
        return
    from agent_sandbox import client as client_module

    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AGENT_SANDBOX_ENV", raising=False)
    monkeypatch.setattr(client_module, "REPO_ROOT_ENV", home / "repo-root.env")
    yield


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Return the absolute path to the test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_csv_content(fixtures_dir: Path) -> bytes:
    """Return raw bytes of sample CSV dataset."""
    return (fixtures_dir / "sample_data.csv").read_bytes()


@pytest.fixture
def sample_analysis_code(fixtures_dir: Path) -> str:
    """Return text content of sample analysis Python script."""
    return (fixtures_dir / "sample_analysis.py").read_text(encoding="utf-8")


@pytest.fixture
def agent_client():
    """Provide an authenticated agentplatform.Client."""
    return get_agent_client()


@pytest.fixture
def runtime_name() -> str:
    """Provide the active parent Agent Runtime name."""
    return get_runtime_name()
