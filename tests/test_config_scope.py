"""Project/user scoped configuration and per-runtime cleanup."""

import json
import os
from pathlib import Path

import pytest

from agent_sandbox import cli, client as client_module
from agent_sandbox.state import StateStore, TrackedSandbox
from fakes import RUNTIME, FakeClient


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A fake git project under the (isolated) home, with cwd inside a sub-directory."""
    root = Path(os.environ["HOME"]) / "work" / "my-repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    for key in ("GOOGLE_CLOUD_PROJECT", "AGENT_RUNTIME_NAME", "SANDBOX_TEMPLATE_NAME", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(key, raising=False)
    return root


def test_plain_dotenv_in_cwd_is_never_read(repo: Path):
    (repo / ".env").write_text('export GOOGLE_CLOUD_PROJECT="someone-elses-project"\n')
    (repo / "src" / "deep" / ".env").write_text('export GOOGLE_CLOUD_PROJECT="someone-elses-project"\n')
    client_module.load_environment()
    assert "GOOGLE_CLOUD_PROJECT" not in os.environ


def test_project_file_is_found_from_a_subdirectory_and_beats_user_config(repo: Path):
    client_module.user_env_path().parent.mkdir(parents=True)
    client_module.user_env_path().write_text('export GOOGLE_CLOUD_PROJECT="user-level"\nexport GOOGLE_CLOUD_LOCATION="us-central1"\n')
    (repo / ".agent-sandbox.env").write_text('export GOOGLE_CLOUD_PROJECT="repo-level"\n')
    client_module.load_environment()
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "repo-level"     # project wins
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"   # user config fills the gaps


def test_real_environment_beats_every_file(repo: Path, monkeypatch):
    (repo / ".agent-sandbox.env").write_text('export GOOGLE_CLOUD_PROJECT="repo-level"\n')
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-shell")
    client_module.load_environment()
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "from-shell"


def test_explicit_env_file_wins_as_write_target_even_if_it_does_not_exist_yet(repo: Path, monkeypatch, tmp_path):
    # Regression: a not-yet-existing AGENT_SANDBOX_ENV used to be skipped, and the write fell
    # through to the repo-level .env, clobbering a developer's real configuration.
    real = client_module.REPO_ROOT_ENV
    real.write_text('export GOOGLE_CLOUD_PROJECT="precious"\n')
    target = tmp_path / "new" / "env"
    monkeypatch.setenv("AGENT_SANDBOX_ENV", str(target))
    assert cli.main(["config", "set", "FOO=bar", "--format", "json"]) == 0
    assert 'export FOO="bar"' in target.read_text()
    assert real.read_text() == 'export GOOGLE_CLOUD_PROJECT="precious"\n'


def test_config_set_with_project_scope_writes_at_the_git_root(repo: Path, capsys):
    assert cli.main(["config", "set", "--scope", "project", "AGENT_RUNTIME_NAME=projects/1/locations/l/reasoningEngines/9"]) == 0
    written = (repo / ".agent-sandbox.env").read_text()
    assert 'AGENT_RUNTIME_NAME="projects/1/locations/l/reasoningEngines/9"' in written
    assert not (Path.cwd() / ".agent-sandbox.env").exists()   # not in the sub-directory


def test_config_init_writes_all_keys_for_the_setup_script(repo: Path):
    assert cli.main(["config", "init", "--scope", "project", "--project", "my-proj", "--location", "europe-west8"]) == 0
    text = (repo / ".agent-sandbox.env").read_text()
    for needle in ['PROJECT_ID="my-proj"', 'LOCATION="europe-west8"', 'KEY_RING="agent-engine"',
                   'KMS_KEY_NAME="projects/my-proj/locations/europe-west8/keyRings/agent-engine/cryptoKeys/agent-engine-key"']:
        assert needle in text


def test_config_show_reports_where_each_value_came_from(repo: Path, capsys):
    (repo / ".agent-sandbox.env").write_text('export GOOGLE_CLOUD_PROJECT="repo-level"\n')
    cli.main(["config", "show", "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert out["values"]["GOOGLE_CLOUD_PROJECT"] == "repo-level"
    assert out["sources"]["GOOGLE_CLOUD_PROJECT"].endswith(".agent-sandbox.env")
    assert out["project_root"] == str(repo.resolve())


# ---------------------------------------------------------------- per-runtime cleanup
RT_A = "projects/111/locations/europe-west8/reasoningEngines/1"
RT_B = "projects/222/locations/us-central1/reasoningEngines/2"


@pytest.fixture
def two_runtimes(tmp_path: Path, monkeypatch):
    clients = {RT_A: FakeClient(), RT_B: FakeClient()}
    monkeypatch.setenv("AGENT_SANDBOX_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cli, "_client_for_runtime", lambda runtime: clients[runtime])
    monkeypatch.setattr(cli, "_client_and_runtime", lambda: (clients[RT_A], RT_A))
    store = StateStore()
    for rt, project_dir in ((RT_A, "/work/a"), (RT_B, "/work/b")):
        fake = clients[rt].sandboxes
        name = fake.create(name=rt, config={"display_name": "asbx-x", "ttl": "300s"}).response.name
        store.add(TrackedSandbox(name=name, kind="code", display_name="asbx-x", runtime=rt,
                                 created_at=0.0, ttl_seconds=300, project_dir=project_dir))
    return clients, store


def test_cleanup_deletes_each_sandbox_through_its_own_runtimes_client(two_runtimes, capsys):
    clients, store = two_runtimes
    assert cli.main(["cleanup", "--format", "json"]) == 0
    assert len(clients[RT_A].sandboxes.deleted) == 1 and len(clients[RT_B].sandboxes.deleted) == 1
    assert store.list() == []


def test_cleanup_here_only_touches_the_current_project(two_runtimes, monkeypatch, capsys):
    clients, store = two_runtimes
    monkeypatch.setattr("agent_sandbox.client.project_root", lambda start=None: Path("/work/a"))
    cli.main(["cleanup", "--here", "--format", "json"])
    assert len(clients[RT_A].sandboxes.deleted) == 1 and clients[RT_B].sandboxes.deleted == []
    assert [t.runtime for t in store.list()] == [RT_B]


def test_status_covers_every_runtime_in_the_ledger(two_runtimes, capsys):
    cli.main(["status", "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert out["running"] == 2 and {r["runtime"] for r in out["sandboxes"]} == {RT_A, RT_B}


def test_status_keeps_entries_of_an_unreachable_runtime(two_runtimes, monkeypatch, capsys):
    clients, store = two_runtimes

    def flaky(runtime):
        if runtime == RT_B:
            raise RuntimeError("403 PERMISSION_DENIED")
        return clients[runtime]

    monkeypatch.setattr(cli, "_client_for_runtime", flaky)
    code = cli.main(["status", "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_ERROR and RT_B in out["errors"]
    assert {t.runtime for t in store.list()} == {RT_A, RT_B}   # not pruned: we could not verify RT_B
