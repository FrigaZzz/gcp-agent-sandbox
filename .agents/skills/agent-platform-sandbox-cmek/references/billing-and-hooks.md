# Cost safety: billing is per second

A sandbox costs money from creation until it is deleted or expires. The tooling defends in layers;
no single layer is trusted alone.

| Layer | What it does | Fails when |
|---|---|---|
| 1. `run` always deletes | Context manager + SIGTERM/SIGHUP handler delete the sandbox on success, error, Ctrl-C | `kill -9`, power loss |
| 2. Short TTL | Every sandbox is created with a server-side TTL (default 900 s, cap 3600 s). The platform removes it even if the client died | – (this is the backstop) |
| 3. Ledger | Every CLI-created sandbox is recorded in `~/.agent-sandbox/state.json` so `stop`/`cleanup` can find it later | file deleted (use `cleanup --orphans`) |
| 4. Skill rules | `SKILL.md` tells Claude: prefer `run`, `stop` before the final answer, check `status` | the model forgets |
| 5. **SessionEnd hook** | Runs `agent-sandbox cleanup` when the Claude Code session ends | crash/kill -9 of Claude Code, `agent-sandbox` not on the hook's PATH |
| 6. SessionStart hook | Prints a warning into Claude's context if sandboxes are already running (leftovers from a crashed session) | – |

Observed (once, SDK 2.3.0): `expire_time` did **not** move after several `execute_code` calls even though
the docs say execution renews the TTL. Do not count on renewal; size `--ttl` for the whole job.

## Installing the end-of-session hook

**Installed as a Claude Code plugin? Nothing to do:** `hooks/hooks.json` registers both hooks and calls the
bundled `bin/agent-sandbox`. Otherwise pick one; all call the same command, `agent-sandbox cleanup --quiet`.

**A. Permanent, recommended** (merges into your settings, preserves everything else, idempotent):

```bash
agent-sandbox hooks install --scope user       # ~/.claude/settings.json, all projects
agent-sandbox hooks install --scope project    # .claude/settings.json, shared with the team
agent-sandbox hooks install --scope local      # .claude/settings.local.json, just you
agent-sandbox hooks install --scope user --uninstall
```

**B. Manual:** `agent-sandbox hooks print` outputs the JSON block (also in `hooks/settings.snippet.json`).
Merge it into the `hooks` key of a settings file.

**C. Skill-scoped:** this skill's frontmatter already declares a `SessionEnd` hook, which Claude Code
registers when the skill is invoked and keeps for the rest of the session. It only covers sessions in
which the skill was actually used; install A as well if you want guaranteed coverage.

What gets installed:

```json
{"hooks": {
  "SessionEnd":   [{"matcher": "*", "hooks": [{"type": "command", "command": "agent-sandbox cleanup --quiet || true", "timeout": 60}]}],
  "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [{"type": "command", "command": "agent-sandbox status --brief || true", "timeout": 30}]}]
}}
```

## Things to know

* **Timeout.** Claude Code gives SessionEnd hooks a shared 1.5 s budget unless the hook sets
  `timeout` (honoured up to 60 s). The installed entry sets 60. With an empty ledger `cleanup` returns in
  < 0.5 s and needs no credentials; deleting a sandbox takes a second or two.
* **PATH.** The hook runs in a non-interactive shell. `agent-sandbox` must be on that PATH:
  `uv tool install <repo>` puts it in `~/.local/bin`; with a virtualenv install, use the absolute path
  to the venv's `agent-sandbox` in the hook command instead.
* **Credentials.** Deleting needs valid ADC. If `gcloud auth application-default login` has expired the
  hook fails and the TTL is what saves you. `doctor` shows credential state.
* **Scope.** `cleanup` deletes every sandbox started from *this machine* (each through its own runtime's
  project/region), so it also stops sandboxes that another concurrent Claude session is using. Claude Code
  does not expose a session id to shell commands, so per-session scoping is not possible. With parallel
  sessions in different repos use `agent-sandbox cleanup --here` (only the current git project's sandboxes);
  or prefer `run` (self-contained) over `start`.
* **Plugin launcher fast path.** With an empty ledger the plugin's `bin/agent-sandbox` exits immediately for
  `cleanup` and `status --brief` without starting Python, so the hooks cost nothing in sessions that never
  used a sandbox.
* **Not covered.** Hooks do not run when Claude Code is killed hard. Check
  `agent-sandbox status` after any crash, and keep TTLs short.

## Verify the setup

```bash
agent-sandbox start --ttl 5m && agent-sandbox status
echo '{}' | agent-sandbox cleanup --quiet; agent-sandbox status     # nothing should be listed
```

Then exit a Claude Code session that left a sandbox running and check `agent-sandbox status` from a
new terminal.

## Auditing in the cloud

`agent-sandbox status` shows everything in the runtime (including untracked). In the console or with
`gcloud`, filter on the runtime's sandbox environments; display names created by this tool start with
`asbx-`. A runtime itself (`reasoningEngines/…`) is the parent and is not deleted by `cleanup`.
