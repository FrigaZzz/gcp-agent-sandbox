"""CLI behaviour against a fake SDK client: the per-second-billing guarantees."""

import json
from pathlib import Path

import pytest

from agent_sandbox import cli
from fakes import RUNTIME, FakeClient, file_chunk, json_chunk


@pytest.fixture
def fake(tmp_path: Path, monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setenv("AGENT_SANDBOX_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cli, "_client_and_runtime", lambda: (client, RUNTIME))
    monkeypatch.setattr(cli, "_client_for_runtime", lambda runtime: client)
    monkeypatch.chdir(tmp_path)
    return client


def call(capsys, *argv: str):
    code = cli.main(["--format", "json", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


def test_run_deletes_sandbox_and_returns_stdout(fake, capsys):
    code, out = call(capsys, "run", "--code", "print('ok')")
    assert code == 0 and out["stdout"] == "ok\n" and out["sandbox_deleted"]
    assert fake.sandboxes.live == {} and len(fake.sandboxes.deleted) == 1


def test_run_deletes_sandbox_when_execution_raises(fake, capsys):
    def boom(**_):
        raise RuntimeError("network down")

    fake.sandboxes.execute_code = boom
    code, out = call(capsys, "run", "--code", "x")
    assert code == 1 and "network down" in out["error"]
    assert fake.sandboxes.live == {}, "sandbox must be deleted even if execution blows up"


def test_run_remote_failure_is_exit_3_and_still_cleans_up(fake, capsys):
    fake.sandboxes.next_response = [json_chunk(stderr="Traceback...\nValueError: boom", exit_status=106)]
    code, out = call(capsys, "run", "--code", "raise ValueError('boom')")
    assert code == cli.EXIT_REMOTE_FAILED and not out["ok"] and out["exit_status"] == 106
    assert "ValueError: boom" in out["stderr"]
    assert fake.sandboxes.live == {}


def test_run_saves_returned_files_including_subdirs(fake, capsys, tmp_path):
    fake.sandboxes.next_response = [json_chunk(stdout="done"), file_chunk("out/report.json", b'{"n": 2}')]
    code, out = call(capsys, "run", "--code", "x", "--out", "res")
    assert code == 0
    assert (tmp_path / "res/out/report.json").read_text() == '{"n": 2}'
    assert out["files"][0]["name"] == "out/report.json"


def test_run_rejects_path_traversal_in_returned_file(fake, capsys):
    fake.sandboxes.next_response = [json_chunk(), file_chunk("../../etc/evil", b"x")]
    code, out = call(capsys, "run", "--code", "x", "--out", "res")
    assert code == 1 and "Security" in out["error"]
    assert fake.sandboxes.live == {}


def test_run_attaches_uploads(fake, capsys, tmp_path):
    (tmp_path / "data.csv").write_bytes(b"a,b\n1,2\n")
    call(capsys, "run", "--code", "x", "--upload", "data.csv", "--upload", "data.csv:in/renamed.csv")
    names = [f["name"] for f in fake.sandboxes.calls[0]["files"]]
    assert names == ["data.csv", "in/renamed.csv"]
    assert fake.sandboxes.calls[0]["files"][0]["content"] == b"a,b\n1,2\n"


def test_run_requires_exactly_one_code_source(fake, capsys):
    code, out = call(capsys, "run")
    assert code == cli.EXIT_USAGE and fake.sandboxes.created == []


def test_ttl_cap_blocks_expensive_requests_before_any_cloud_call(fake, capsys):
    code, out = call(capsys, "start", "--ttl", "24h")
    assert code == cli.EXIT_ERROR or code == cli.EXIT_USAGE
    assert fake.sandboxes.created == []


def test_start_tracks_sandbox_and_sets_server_side_ttl(fake, capsys):
    code, out = call(capsys, "start", "--ttl", "5m")
    assert code == 0 and out["ttl_seconds"] == 300 and "billed per second" in out["billing"]
    assert fake.sandboxes.created[0]["config"]["ttl"] == "300s"
    assert fake.sandboxes.created[0]["config"]["display_name"].startswith("asbx-")
    _, status = call(capsys, "status")
    assert status["running"] == 1 and status["sandboxes"][0]["tracked"]


def test_exec_uses_current_sandbox_and_stop_deletes_it(fake, capsys):
    call(capsys, "start")
    code, out = call(capsys, "exec", "--code", "print('ok')")
    assert code == 0 and out["stdout"] == "ok\n"
    code, out = call(capsys, "stop")
    assert code == 0 and len(out["deleted"]) == 1 and fake.sandboxes.live == {}
    _, status = call(capsys, "status")
    assert status["running"] == 0


def test_cleanup_deletes_everything_tracked(fake, capsys):
    call(capsys, "start")
    call(capsys, "start")
    assert len(fake.sandboxes.live) == 2
    code, out = call(capsys, "cleanup")
    assert code == 0 and len(out["deleted"]) == 2 and fake.sandboxes.live == {}


def test_cleanup_with_nothing_tracked_makes_no_cloud_call(fake, capsys, monkeypatch):
    def forbidden():
        raise AssertionError("cleanup must not touch the SDK when the ledger is empty")

    monkeypatch.setattr(cli, "_client_and_runtime", forbidden)
    code = cli.main(["cleanup", "--quiet"])
    assert code == 0 and capsys.readouterr().out == ""


def test_cleanup_treats_already_expired_sandbox_as_deleted(fake, capsys):
    call(capsys, "start")
    fake.sandboxes.live.clear()  # server-side TTL already removed it
    code, out = call(capsys, "cleanup")
    assert code == 0 and out["failed"] == []
    assert cli.StateStore().list() == []


def test_cleanup_reports_failure_and_keeps_ledger_entry_for_retry(fake, capsys):
    call(capsys, "start")
    fake.sandboxes.delete_error = RuntimeError("503 unavailable")
    code, out = call(capsys, "cleanup")
    assert code == 1 and len(out["failed"]) == 1
    assert len(cli.StateStore().list()) == 1


def test_cleanup_orphans_only_removes_prefixed_untracked(fake, capsys):
    from types import SimpleNamespace

    fake.sandboxes.live["other/1"] = SimpleNamespace(name="other/1", display_name="someone-elses",
                                                     state="s", create_time=None, expire_time=None)
    fake.sandboxes.live["mine/2"] = SimpleNamespace(name="mine/2", display_name="asbx-leaked",
                                                    state="s", create_time=None, expire_time=None)
    code, out = call(capsys, "cleanup", "--orphans")
    assert out["deleted"] == ["mine/2"] and "other/1" in fake.sandboxes.live


def test_cleanup_everything_requires_confirmation(fake, capsys):
    code, out = call(capsys, "cleanup", "--everything")
    assert code == cli.EXIT_USAGE


def test_status_brief_is_silent_when_nothing_tracked(fake, capsys):
    assert cli.main(["status", "--brief"]) == 0
    assert capsys.readouterr().out == ""


def test_status_brief_warns_about_running_sandboxes(fake, capsys):
    call(capsys, "start")
    assert cli.main(["status", "--brief"]) == 0
    assert "billed per second" in capsys.readouterr().out


def test_status_prunes_ledger_entries_that_no_longer_exist(fake, capsys):
    call(capsys, "start")
    fake.sandboxes.live.clear()
    _, out = call(capsys, "status")
    assert out["running"] == 0 and len(out["pruned_stale_ledger_entries"]) == 1


def test_stdout_truncation_saves_full_text(fake, capsys, tmp_path):
    fake.sandboxes.next_response = [json_chunk(stdout="x" * 500)]
    _, out = call(capsys, "run", "--code", "x", "--max-output-chars", "100", "--out", "res")
    assert out["stdout_truncated"] and len(out["stdout"]) == 100
    assert len((tmp_path / "res/_stdout.txt").read_text()) == 500


def test_auto_format_is_json_when_not_a_tty(fake, capsys):
    cli.main(["status"])  # pytest captures stdout: not a TTY
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize("message,needle", [
    ("400 FAILED_PRECONDITION restrictNonCmekServices", "runtime create"),
    ("Provided CryptoKey cannot be used in Vertex AI service", "BOTH"),
    ("scope is required but not consented", "checkbox"),
    ("Reauthentication failed", "application-default login"),
])
def test_known_errors_get_actionable_hints(message, needle):
    assert needle in cli.hint_for(RuntimeError(message))


def test_format_flag_works_before_and_after_the_subcommand(fake, capsys):
    assert cli.main(["status", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    assert cli.main(["--format", "json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
