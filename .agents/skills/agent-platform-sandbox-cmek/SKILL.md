---
name: agent-platform-sandbox-cmek
description: >-
  Run Python code, bash commands and file-processing jobs safely inside Google Cloud Agent
  Platform Sandboxes (CMEK-encrypted, billed per second) using the `agent-sandbox` CLI: start,
  send code/files, retrieve outputs, check status and always stop. Also covers one-time setup
  (Cloud KMS key, dual service-agent IAM, parent runtime, ADC) and a troubleshooting matrix.
  Use when asked to execute untrusted/generated code or scripts in a GCP sandbox, process data
  remotely in isolation, set up or debug Agent Platform sandboxes/CMEK, or stop leftover sandboxes.
hooks:
  SessionEnd:
    - hooks:
        - type: command
          command: "agent-sandbox cleanup --quiet || true"
          timeout: 60
---

# Agent Platform Sandbox (CMEK)

Isolated, Google-managed Linux sandboxes for running code that should not run on the user's
machine. Encryption is CMEK (Cloud KMS key) because the organisation policy requires it.
**Sandboxes are billed per second for as long as they exist.** Two jobs:

* **Use it** — run code/files in a sandbox with the `agent-sandbox` CLI (sections below).
* **Set it up / fix it** — first-time provisioning and errors → `references/setup-cmek.md`,
  `references/troubleshooting.md`.

## Cost rules (follow every time)

1. **Prefer `agent-sandbox run`.** It creates, executes, saves outputs and *always* deletes the
   sandbox, even on errors, Ctrl-C or SIGTERM. Use it for any single job.
2. Use `start` only when several dependent steps must share state (files persist between calls).
   Then **you must `stop` it before your final answer — also when a step failed.** Do not leave a
   sandbox for a later turn unless the user explicitly asks you to.
3. Never start a sandbox "just in case", for a plan, or before you have code to run.
4. Keep `--ttl` as small as the job allows (default 900s, hard cap 3600s). It is a server-side
   backstop that deletes the sandbox even if everything else fails; it is not a substitute for `stop`.
5. Before finishing any task that touched a sandbox, run `agent-sandbox status`. If anything is
   listed that you started, run `agent-sandbox cleanup`. Report to the user that none remain.
6. A SessionEnd hook runs `agent-sandbox cleanup` when the chat ends (the plugin installs it; otherwise
   `agent-sandbox hooks install`, and this skill's frontmatter declares it too). It is a safety net:
   see `references/billing-and-hooks.md`.

## Preflight (once per task)

```bash
agent-sandbox config show       # which project/region/runtime will be billed, and which file each value came from
agent-sandbox doctor            # env vars, credentials, runtime + CMEK, sandbox listing
agent-sandbox doctor --live     # also creates/deletes a sandbox (~3s) to prove end-to-end
```

* **Know whose project you are billing.** Config can be project-scoped: a repository may carry its own
  `.agent-sandbox.env` (its own runtime/template for custom code execution). Read the `config show` output
  and tell the user which project/runtime you are about to use if it is not obvious. A plain `.env` is never read.
* Not configured yet: ask the user for the GCP project (and region if not `europe-west8`), then
  `agent-sandbox config init --project <id> [--scope project]` and follow `references/setup-cmek.md`
  (`config set KEY=VALUE` for a single value such as `AGENT_RUNTIME_NAME`).
* `agent-sandbox` is on PATH when installed as a plugin (the first call builds its environment, which can
  take up to a minute: run `doctor` first with a generous timeout). Otherwise `uv tool install <repo-root>`
  or `<repo-root>/scripts/install.sh`. If `doctor` reports `adc` failed, ask the user to run
  `gcloud auth application-default login`.
* Failures print a `hint`; the full matrix is in `references/troubleshooting.md`.

## One-shot run (default)

```bash
# Python from a file, with an input file; outputs land in ./sandbox-results
agent-sandbox run --file analysis.py --upload data.csv --out ./sandbox-results

# inline code, or stdin
agent-sandbox run --code 'print(sum(range(10)))'
cat script.py | agent-sandbox run --stdin

# a shell command
agent-sandbox run --bash 'python3 --version && ls -la'
```

* The script runs with working directory `/home/bard`; uploaded files appear there under their
  basename (or `LOCAL:REMOTE` to choose a path, subdirectories are created).
* **Only files the script creates under the working directory are returned**, with subdirectory
  names preserved, and saved under `--out`. Write results to a file (`report.json`, `out/x.png`);
  anything under `/tmp` or outside the working directory is not returned.
* Large stdout is truncated at 20,000 chars (`--max-output-chars`); the full text is saved to
  `<out>/_stdout.txt`.
* Environment (observed): Python 3.12 with ~140 packages preinstalled (numpy, pandas, scipy, scikit-learn,
  matplotlib, tensorflow, spacy, opencv, openpyxl, …), plus bash, git, curl, gcc and tar; 8 vCPU visible
  (~2x parallel speedup measured) / **2 GB RAM** (exceeding it kills the run silently with `exit_status: -1`); runs
  as root in `/home/bard`. **No outbound internet** (DNS fails), so `pip install` of new packages and downloads will
  not work: use what is preinstalled. Full inventory, limits and quirks: `references/sandbox-environment.md`.

## Multi-step session (only when state must persist)

```bash
agent-sandbox start --ttl 15m --name etl          # prints the sandbox id; becomes "current"
agent-sandbox upload ./big.csv data/big.csv
agent-sandbox exec --file step1.py                # Python; files created are returned
agent-sandbox bash 'wc -l data/big.csv'           # shell command
agent-sandbox download results/summary.json --out ./sandbox-results   # file, or dir as .tar.gz
agent-sandbox stop                                # REQUIRED. `stop --all` stops everything started here
```

Use `--sandbox <id>` when more than one is running (`agent-sandbox status` lists them).
Output files from `exec`/`bash` are only those *created* by that call; to fetch an older file use
`download` (it copies, returns and removes a scratch copy for you).

## Reading results

JSON is printed automatically when stdout is not a terminal (force with `--format json`):

```json
{"ok": false, "exit_status": 106, "stdout": "", "stderr": "...Traceback...",
 "files": [{"name": "out/report.json", "bytes": 8, "saved_to": "./sandbox-results/out/report.json"}],
 "sandbox": "projects/.../sandboxEnvironments/123", "sandbox_deleted": true, "elapsed_s": 2.7}
```

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | Ran and succeeded | Read `stdout` / `files` |
| 3 | Code ran but **failed** (`exit_status` ≠ 0; Python exceptions give 106) | Read `stderr`, fix the code, rerun |
| 1 | Tooling/cloud error (`error` + `hint` fields) | Follow the hint; see troubleshooting |
| 2 | Bad arguments | Fix the command |

## What may go into a sandbox

* Treat the sandbox as disposable and isolated, **not** as a place for secrets: never upload
  `.env`, keys, tokens or ADC files; the container does not inherit your credentials.
* Do not run code the user has not seen when it deletes, exfiltrates or provisions anything outside
  the sandbox; the sandbox protects the local machine, not remote services the code can reach.
* Upload limit is 100 MB per request. Sandboxes are not storage: download what you need before
  `stop` (deletion and TTL expiry destroy all files).
* Code sandbox (`--kind code`, default) is the verified path under CMEK. Shell sandboxes
  (`--kind shell`) need a template and often fail under CMEK in preview regions:
  `references/shell-sandboxes.md`.

## First-time setup (admin / integrator)

Order: enable APIs → create Cloud KMS key in the **same region** → grant the key to **both** service
agents → `agent-sandbox runtime create` → `agent-sandbox doctor --live`. Details, roles, org-policy
notes and a ready-to-send request for the cloud/KMS team: `references/setup-cmek.md`.
Scripted version: `scripts/setup_kms.sh` (plugin: `${CLAUDE_PLUGIN_ROOT}/scripts/setup_kms.sh`); it reads
the same config files as the CLI, so run `agent-sandbox config init` first.

## Reference index

| File | Read it when |
|---|---|
| `references/cli-reference.md` | exact flags, env vars, output schemas, exit codes |
| `references/setup-cmek.md` | provisioning KMS/IAM/runtime, permissions per role |
| `references/troubleshooting.md` | any error message or odd behaviour |
| `references/billing-and-hooks.md` | cost safety layers, installing the end-of-session hook |
| `references/sdk-contracts.md` | writing Python against `agentplatform` directly |
| `references/shell-sandboxes.md` | bash/container sandboxes and templates |
| `references/sandbox-environment.md` | what is installed (Python, libs, tools), measured RAM/CPU/disk/time limits, execution quirks, a "will my job fit" checklist |
| `references/python-packages.txt` | exact `name==version` list of every preinstalled Python package (grep it before assuming a lib exists) |
