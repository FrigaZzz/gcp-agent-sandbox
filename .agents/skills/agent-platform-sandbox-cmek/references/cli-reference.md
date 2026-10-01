# `agent-sandbox` CLI reference

Install from the repository root: `uv tool install .` (or `pip install -e .`). From a checkout without
installing: `python cli.py <command>`. Python ≥ 3.10; the SDK is pinned to
`google-cloud-aiplatform[agent_engines]==2.3.0` (API `v1beta1`, Preview).

Global flag (accepted before or after the command): `--format auto|json|text`.
`auto` prints JSON when stdout is not a terminal (so an agent always gets JSON) and text on a TTY.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Tooling / cloud / credentials error. JSON has `error` and, when recognised, `hint` |
| 2 | Usage error (bad or missing arguments) |
| 3 | The remote code/command ran but failed (`exit_status` ≠ 0, details in `stderr`) |
| 130 | Interrupted (Ctrl-C). `run` still deletes its sandbox |

## Commands

| Command | Purpose |
|---|---|
| `doctor [--live]` | Check env vars, ADC, SDK, runtime exists **with CMEK**, sandbox listing. `--live` creates, runs `print(6*7)` in and deletes a sandbox (~3 s billed) |
| `run` | **One-shot**: create → (upload) → execute → save outputs → always delete |
| `start` | Create a long-lived sandbox, record it in the ledger, make it "current" |
| `exec` | Run Python in a started code sandbox |
| `bash COMMAND` | Run a bash command in a started sandbox (code or shell kind) |
| `upload LOCAL [REMOTE]` | Upload one file (≤ 100 MB) |
| `download REMOTE` | Download a file, or a directory as `<name>.tar.gz` |
| `status [--brief]` | Running sandboxes, age, remaining TTL; prunes dead ledger entries |
| `stop [ID] [--all]` | Delete one sandbox (default: current) or all started from this machine |
| `cleanup [--here] [--orphans] [--everything --yes] [--quiet]` | Delete everything this machine started; hook-friendly |
| `runtime create\|info` | Create the CMEK parent runtime (writes `AGENT_RUNTIME_NAME`) / show it |
| `templates create\|list` | Shell sandbox templates (writes `SANDBOX_TEMPLATE_NAME`) |
| `config show\|set KEY=VALUE…\|init --project P [--scope project\|user]` | Show where each setting comes from / write config. `init` writes every variable `setup_kms.sh` needs |
| `hooks print\|install [--scope user\|project\|local] [--uninstall]` | Claude Code SessionStart/SessionEnd hooks (the plugin installs them itself) |
| `repl` | Interactive prompt in a fresh sandbox, for humans (`!ls`, `upload f`, `exit`) |

### `run` / `exec` input

Exactly one of `--code TEXT`, `--file PATH`, `--stdin` (`run` also accepts `--bash CMD [--cwd DIR] [--timeout S]`).
`--upload LOCAL[:REMOTE]` is repeatable. `--out DIR` (default `./sandbox-results`).
`--max-output-chars N` (default 20000, `0` = unlimited). `run` also takes `--ttl` (default `900s`).

### `start`

`--kind code|shell` (default `code`), `--ttl` (e.g. `900`, `900s`, `15m`, `1h`; capped), `--name LABEL`,
`--template NAME` (shell). Sandbox display names are prefixed `asbx-` so leftovers can be found with
`cleanup --orphans`.

### `cleanup` scope

| Invocation | Deletes |
|---|---|
| `cleanup` | every sandbox in this machine's ledger (`~/.agent-sandbox/state.json`), each through **its own** runtime's project/region |
| `cleanup --here` | only sandboxes started from the current git project (parallel sessions in different repos) |
| `cleanup --orphans` | + untracked sandboxes in the runtime whose display name starts with `asbx-` |
| `cleanup --everything --yes` | **all** sandboxes in the runtime, including other people's |

With an empty ledger and no flags it makes no cloud call, needs no credentials and returns in < 0.5 s.
`stop projects/.../sandboxEnvironments/ID` deletes an untracked sandbox only because you named it.

## Result schemas

`run` / `exec` / `bash`:

```json
{
  "ok": true, "exit_status": 0, "stdout": "...", "stderr": "...",
  "files": [{"name": "out/report.json", "bytes": 8, "saved_to": "sandbox-results/out/report.json"}],
  "stdout_truncated": true, "stdout_full_path": "sandbox-results/_stdout.txt",
  "sandbox": "projects/…/sandboxEnvironments/ID", "sandbox_deleted": true, "elapsed_s": 2.7
}
```

`start`: `{"ok", "sandbox", "id", "kind", "ttl_seconds", "expires_at", "billing"}`.
`status`: `{"ok", "running", "sandboxes": [{"id","name","state","kind","tracked","age_seconds","expires_at","remaining_seconds"}], "pruned_stale_ledger_entries", "current"}`.
`stop` / `cleanup`: `{"ok", "deleted": [...], "failed": [...]}`.
`doctor`: `{"ok", "checks": [{"check","ok","detail","hint"}]}`.
Errors: `{"ok": false, "error": "...", "hint": "..."}`.

## Configuration

The real environment wins; files only fill gaps. Lookup order (first file defining a variable wins):

1. `$AGENT_SANDBOX_ENV`
2. nearest **`.agent-sandbox.env`** walking up from the current directory, never above `$HOME`: a repository's
   own sandbox (project/runtime/template). `config init|set --scope project` writes `<git root>/.agent-sandbox.env`
3. `~/.config/agent-sandbox/env` (`--scope user`)
4. `<repo>/.env` (source checkouts of this tool only)

A plain `./.env` is never read. `config show` prints each value with the file it came from. Writes
(`runtime create`, `templates create`, `config set`) go to `$AGENT_SANDBOX_ENV` if set, else the highest-priority
existing file, else the per-user file. Lines look like `export NAME="value"`; `${VAR}` expansion is supported.

| Variable | Meaning | Default |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Workload project id | required |
| `GOOGLE_CLOUD_LOCATION` | Region (must match the KMS key) | `europe-west8` |
| `KMS_KEY_NAME` | `projects/…/locations/…/keyRings/…/cryptoKeys/…` (no `/cryptoKeyVersions/N`) | needed for `runtime create` |
| `AGENT_RUNTIME_NAME` | Parent runtime (`…/reasoningEngines/ID`) | written by `runtime create` |
| `SANDBOX_TEMPLATE_NAME` | Reusable shell template | written by `templates create` |
| `AGENT_SANDBOX_DEFAULT_TTL` | Default TTL seconds | 900 |
| `AGENT_SANDBOX_MAX_TTL` | Hard cap on any requested TTL, seconds | 3600 |
| `AGENT_SANDBOX_STATE` | Ledger path | `~/.agent-sandbox/state.json` |
| `AGENT_SANDBOX_ENV` | Explicit env file | – |

## Python API (same behaviour as the CLI)

```python
from agent_sandbox import CodeExecutionSandbox, StateStore

with CodeExecutionSandbox(ttl="10m", tracker=StateStore()) as sb:      # deleted on exit, even on error
    sb.put("data.csv", b"a,b\n1,2\n")
    result = sb.run("print(open('data.csv').read())")                    # ExecResult
    result.ok, result.stdout, result.stderr, result.exit_status, result.files
    blob = sb.fetch("data.csv")                                          # OutputFile(name, data)
    res = sb.run_bash("ls -la")                                          # exit_status is the command's
```

`execute()` / `execute_code()` / `run_command()` keep their raising behaviour (non-zero exit →
`RuntimeError` carrying the remote traceback). `BaseSandbox.attach(name)` wraps a running sandbox.
