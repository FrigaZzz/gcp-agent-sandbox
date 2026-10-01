# gcp-agent-sandbox

Claude Code plugin, skill and CLI for running code in Google Cloud Agent Platform sandboxes, with CMEK encryption and safe per-second-billing cleanup.

[MIT licensed](LICENSE)  ·  CLI, Python library and Claude skill in one repo

Run Python and shell commands in isolated **Google Cloud Agent Platform sandboxes** encrypted with a
**customer-managed key (Cloud KMS)**. This repository ships three things that share one codebase:

| What | For | Where |
|---|---|---|
| **`agent-sandbox` CLI** | Anyone: start a sandbox, send code/files, get results back, check status, stop | `src/agent_sandbox/cli.py` |
| **Claude skill** | Teaching Claude (Claude Code, Antigravity, …) to set this up and use it safely | `.agents/skills/agent-platform-sandbox-cmek/` |
| **Python library** | Embedding sandboxes in your own code | `src/agent_sandbox/` |

> **Sandboxes are billed per second while they exist.** Everything here is built to delete them:
> `run` always cleans up, every sandbox gets a short server-side TTL, a local ledger lets `cleanup`
> find leftovers, and an optional Claude Code hook stops them when a chat ends.
> See [billing and hooks](.agents/skills/agent-platform-sandbox-cmek/references/billing-and-hooks.md).

## Install

**Claude Code (recommended): two commands, nothing else to install.**

```text
/plugin marketplace add FrigaZzz/gcp-agent-sandbox
/plugin install agent-sandbox@gcp-agent-sandbox
```

The plugin adds the skill, puts `agent-sandbox` on Claude's PATH (a launcher that builds its own virtualenv on
first use; needs `uv` or Python ≥ 3.10) and registers the **SessionEnd hook that stops billed sandboxes when a
chat ends**. Update with `/plugin marketplace update`. Without the plugin system (other agents, or just the CLI):

```bash
git clone https://github.com/FrigaZzz/gcp-agent-sandbox && cd gcp-agent-sandbox
./scripts/install.sh                      # CLI via uv + skill in ~/.claude/skills + hooks
./scripts/install.sh --skills-dir DIR     # skill into another agent's skills directory
./scripts/install.sh --no-hooks
```

## First-time setup (once per GCP project)

```bash
gcloud auth login && gcloud auth application-default login
agent-sandbox config init --project my-project --scope user     # region defaults to europe-west8
agent-sandbox config show                                       # where each value comes from
./scripts/setup_kms.sh                # APIs, key ring/key, IAM for both service agents (needs KMS admin)
agent-sandbox runtime create          # CMEK parent runtime (saves AGENT_RUNTIME_NAME)
agent-sandbox doctor --live           # prove it works end to end (~3 s billed)
```

(When installed as a plugin the script is at `${CLAUDE_PLUGIN_ROOT}/scripts/setup_kms.sh`; Claude knows the path.)
Details, permissions per role and a request template for your KMS team:
[`references/setup-cmek.md`](.agents/skills/agent-platform-sandbox-cmek/references/setup-cmek.md)


### Project-scoped sandboxes

A repository can have **its own** sandbox (its own project, runtime or template for custom code execution).
Put its settings in `.agent-sandbox.env` at the repo root:

```bash
agent-sandbox config init --scope project --project team-sandbox-project
agent-sandbox runtime create --display-name my-repo-sandbox     # writes AGENT_RUNTIME_NAME into that file
```

Lookup order, first match wins per variable: shell environment → `$AGENT_SANDBOX_ENV` → nearest
`.agent-sandbox.env` walking up from the current directory → `~/.config/agent-sandbox/env` → this repo's `.env`.
A plain `.env` in your current directory is **never** read, so another project's `.env` cannot redirect
billing. Resource names are not secrets (IAM is the gate), so committing `.agent-sandbox.env` is a convenient
way to share a team sandbox. `agent-sandbox config show` prints the file each value came from; check it before
running code in a repo you do not know.

## Using the CLI

```bash
# One-shot (preferred): create → upload → run → save outputs → ALWAYS delete
agent-sandbox run --file analysis.py --upload data.csv --out ./sandbox-results
agent-sandbox run --bash 'python3 -c "import pandas; print(pandas.__version__)"'

# Multi-step session: you must stop it
agent-sandbox start --ttl 15m
agent-sandbox upload ./big.csv data/big.csv
agent-sandbox exec --code 'print(open("data/big.csv").read()[:50])'
agent-sandbox bash 'ls -la data'
agent-sandbox download results.json --out ./sandbox-results      # a directory comes back as .tar.gz
agent-sandbox status                                             # what is running and billing
agent-sandbox stop                                               # or: cleanup (everything started here)

agent-sandbox repl                                               # interactive prompt for humans
```

JSON output is automatic when piped (`--format json|text` to force). Exit codes: `0` ok, `1` tooling
error (with a `hint`), `2` usage, `3` the remote code ran but failed. Full reference:
[`references/cli-reference.md`](.agents/skills/agent-platform-sandbox-cmek/references/cli-reference.md).

What the sandbox looks like (observed): Python 3.12 with numpy, pandas and matplotlib, ~8 vCPU / 2 GB,
runs as root in `/home/bard`, **no outbound internet**. Only files created under the working directory
are returned.

## Using it with Claude

The skill teaches Claude both the one-time setup and safe day-to-day use, with the billing rules written
as instructions.

```bash
# Claude Code, this repository: already discoverable via .claude/skills (symlink)
# Claude Code, everywhere:
ln -s "$PWD/.agents/skills/agent-platform-sandbox-cmek" ~/.claude/skills/agent-platform-sandbox-cmek

```

With the **plugin**, the SessionEnd cleanup and SessionStart warning hooks are already active. With a manual
skill install, run `agent-sandbox hooks install --scope user` (idempotent; `--uninstall` removes it). The skill's
frontmatter also declares the cleanup hook for sessions where the skill is invoked. Parallel sessions in
different repos can use `agent-sandbox cleanup --here` to stop only the current repo's sandboxes.

## Python library

```python
from agent_sandbox import CodeExecutionSandbox, StateStore

with CodeExecutionSandbox(ttl="10m", tracker=StateStore()) as sandbox:     # deleted on exit, even on error
    sandbox.put("data.csv", b"id,val\n1,100\n")
    result = sandbox.run("import csv; print(sum(int(r['val']) for r in csv.DictReader(open('data.csv'))))")
    print(result.ok, result.stdout, result.exit_status)                      # True 100\n 0
    print(sandbox.fetch("data.csv").data)                                    # raw bytes back
```

## Repository layout

```text
├── src/agent_sandbox/            # library + CLI (cli.py), REPL (repl.py), ledger (state.py), hooks (hooks.py)
├── .agents/skills/agent-platform-sandbox-cmek/   # the Claude skill: SKILL.md, references/, hooks/
├── .claude-plugin/               # plugin.json + marketplace.json: makes the repo installable with /plugin
├── hooks/hooks.json              # SessionEnd cleanup + SessionStart warning (plugin hooks)
├── bin/agent-sandbox             # launcher: builds its own venv on first run, on Claude's PATH as a plugin
├── .claude/skills/               # symlink so Claude Code finds the skill when working in this repo
├── scripts/                      # install.sh, setup_kms.sh, create_instance.py, create_shell_template.py
├── examples/                     # runnable demos (code execution, shell, resource inspection)
├── tests/                        # offline unit tests (fake SDK) + live e2e tests
└── cli.py                        # launcher for a source checkout: python cli.py <command>
```

## Tests

```bash
pip install -e ".[dev]"
pytest                 # offline: fake SDK client, no cloud access, no cost
pytest -m e2e          # live: creates and deletes real sandboxes in your runtime (billed, a few seconds)
```

## Key discoveries

1. **Two service agents need the key.** `runtimes.create` validates `service-<N>@gcp-sa-aiplatform…`,
   sandbox execution uses `…@gcp-sa-aiplatform-re…`. Grant both `roles/cloudkms.cryptoKeyEncrypterDecrypter`.
2. **Only newly created files come back** from `execute_code`; downloading an existing file means copying
   it to a new name first (the CLI's `download` does this).
3. **Python exceptions don't raise in the SDK**: they return `exit_status_int` 106 with the traceback in `msg_err`.
4. Under CMEK in preview regions prefer the code sandbox; default shell containers may fail with `INTERNAL`.
5. ADC consent needs the Google Cloud Platform checkbox ticked; `gcloud auth login` and ADC are separate logins.

Full matrix: [`references/troubleshooting.md`](.agents/skills/agent-platform-sandbox-cmek/references/troubleshooting.md).
