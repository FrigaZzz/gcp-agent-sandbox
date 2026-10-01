"""Offline unit tests: response parsing, safe saving, TTL/ledger, hook merging."""

import json
import os
from pathlib import Path

import pytest

from agent_sandbox import hooks
from agent_sandbox.results import parse_response, safe_target, save_files, OutputFile
from agent_sandbox.state import StateStore, TrackedSandbox, parse_ttl
from fakes import RUNTIME, file_chunk, json_chunk
from types import SimpleNamespace


# ---------------------------------------------------------------- parse_response
def test_parse_response_separates_stdout_stderr_exit_and_files():
    response = SimpleNamespace(outputs=[
        json_chunk(stdout="out\n", stderr="warn\n", exit_status=0),
        file_chunk("sub/report.json", b"{}", "application/json"),
    ])
    result = parse_response(response)
    assert result.ok and result.stdout == "out\n" and result.stderr == "warn\n"
    assert [f.name for f in result.files] == ["sub/report.json"]


def test_file_chunks_are_never_mistaken_for_stdout():
    # Regression: text/plain *file* chunks used to be appended to stdout.
    response = SimpleNamespace(outputs=[json_chunk(stdout="hi"), file_chunk("made.txt", b"SECRET")])
    assert parse_response(response).stdout == "hi"


def test_nonzero_exit_status_is_failure_even_without_stderr():
    assert not parse_response(SimpleNamespace(outputs=[json_chunk(exit_status=106)])).ok


def test_stderr_alone_is_not_a_failure():
    assert parse_response(SimpleNamespace(outputs=[json_chunk(stderr="deprecation warning")])).ok


# ---------------------------------------------------------------- safe saving
@pytest.mark.parametrize("name", ["../evil", "a/../../evil", "/abs/path", "", ".", "..", "a\\b"])
def test_safe_target_rejects_escapes(tmp_path: Path, name: str):
    with pytest.raises(ValueError):
        safe_target(tmp_path, name)


def test_safe_target_normalises_redundant_slashes_inside_root(tmp_path: Path):
    assert safe_target(tmp_path, "a//b") == tmp_path.resolve() / "a" / "b"


def test_safe_target_rejects_symlink_components(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        safe_target(out, "link/file.txt")


def test_save_files_creates_subdirectories(tmp_path: Path):
    saved = save_files([OutputFile("a/b/c.txt", b"x"), OutputFile("top.bin", b"\x00\xff")], tmp_path)
    assert (tmp_path / "a/b/c.txt").read_bytes() == b"x"
    assert (tmp_path / "top.bin").read_bytes() == b"\x00\xff"
    assert len(saved) == 2


# ---------------------------------------------------------------- TTL
@pytest.mark.parametrize("value,expected", [(900, 900), ("900", 900), ("900s", 900), ("15m", 900), ("1h", 3600)])
def test_parse_ttl_units(value, expected):
    assert parse_ttl(value) == expected


@pytest.mark.parametrize("value", ["abc", "0", "-5", "10x", ""])
def test_parse_ttl_rejects_garbage(value):
    with pytest.raises(ValueError):
        parse_ttl(value)


def test_parse_ttl_enforces_cost_cap(monkeypatch):
    monkeypatch.delenv("AGENT_SANDBOX_MAX_TTL", raising=False)
    with pytest.raises(ValueError, match="billed per second"):
        parse_ttl("2h")
    monkeypatch.setenv("AGENT_SANDBOX_MAX_TTL", "7200")
    assert parse_ttl("2h") == 7200


# ---------------------------------------------------------------- ledger
def _tracked(name: str, kind: str = "code") -> TrackedSandbox:
    return TrackedSandbox(name=f"{RUNTIME}/sandboxEnvironments/{name}", kind=kind,
                          display_name="asbx-x", runtime=RUNTIME, created_at=0.0, ttl_seconds=900)


def test_ledger_add_resolve_remove(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.add(_tracked("1"))
    store.add(_tracked("2"))
    assert {t.short_id for t in store.list()} == {"1", "2"}
    assert store.resolve().short_id == "2"          # most recently started is current
    assert store.resolve("1").short_id == "1"       # short id
    store.remove(_tracked("2").name)
    assert store.current() is None
    assert store.resolve().short_id == "1"          # single entry resolves without --sandbox


def test_ledger_ambiguous_without_current_raises(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.add(_tracked("1"), make_current=False)
    store.add(_tracked("2"), make_current=False)
    with pytest.raises(LookupError, match="--sandbox"):
        store.resolve()


def test_ledger_survives_corrupt_file(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert StateStore(path).list() == []


# ---------------------------------------------------------------- hooks
def test_merge_hooks_is_idempotent_and_preserves_user_settings():
    existing = {
        "model": "sonnet",
        "hooks": {"SessionEnd": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo mine"}]}]},
    }
    once = hooks.merge_hooks(existing)
    twice = hooks.merge_hooks(once)
    assert once == twice
    assert twice["model"] == "sonnet"
    commands = [h["command"] for g in twice["hooks"]["SessionEnd"] for h in g["hooks"]]
    assert "echo mine" in commands and any("agent-sandbox cleanup" in c for c in commands)
    assert "SessionStart" in twice["hooks"]


def test_session_end_hook_sets_timeout_above_default_budget():
    entry = hooks.snippet()["hooks"]["SessionEnd"][0]["hooks"][0]
    assert entry["timeout"] > 2  # Claude Code's SessionEnd default budget is 1.5s


def test_remove_hooks_restores_user_settings():
    existing = {"hooks": {"SessionEnd": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    restored = hooks.remove_hooks(hooks.merge_hooks(existing))
    assert restored == existing


def test_install_writes_file_and_refuses_invalid_json(tmp_path: Path):
    path = hooks.install("project", project_dir=tmp_path)
    assert json.loads(path.read_text())["hooks"]["SessionEnd"]
    path.write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        hooks.install("project", project_dir=tmp_path)
    assert path.read_text() == "{broken"  # never clobber a file we cannot parse
