"""``agent-sandbox``: scriptable CLI for Google Cloud Agent Platform sandboxes (CMEK).

Designed to be driven by an AI agent *and* by humans:

* Output is JSON when stdout is not a terminal (or with ``--format json``), text otherwise.
* Exit codes: 0 success, 1 tooling/infrastructure error, 2 usage error,
  3 the remote code/command ran but failed (details in the JSON ``exit_status``/``stderr``).
* Sandboxes are billed per second. ``run`` always deletes its sandbox; ``start`` records the
  sandbox in a local ledger so ``stop`` / ``cleanup`` (and the Claude Code SessionEnd hook)
  can always find it; every sandbox gets a short TTL as a server-side backstop.

Heavy SDK imports are deferred so cheap commands (``cleanup`` with nothing to do) stay fast
and work without credentials.
"""

import argparse
import json
import mimetypes
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import hooks as hooks_mod
from .results import safe_target
from .state import (
    DISPLAY_NAME_PREFIX,
    StateStore,
    TrackedSandbox,
    default_ttl_seconds,
    parse_ttl,
)

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_REMOTE_FAILED = 0, 1, 2, 3
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # documented per-request file limit
BILLING_NOTE = (
    "Sandboxes are billed per second until deleted. Run `agent-sandbox stop` when finished."
)

HINTS: List[Tuple[str, str]] = [
    ("restrictNonCmekServices", "The runtime must be created with CMEK: run `agent-sandbox runtime create` "
     "(needs KMS_KEY_NAME). See references/troubleshooting.md."),
    ("cannot be used in Vertex AI service", "Grant roles/cloudkms.cryptoKeyEncrypterDecrypter on the key to BOTH "
     "service-<PROJECT_NUMBER>@gcp-sa-aiplatform and ...@gcp-sa-aiplatform-re service agents (scripts/setup_kms.sh)."),
    ("scope is required but not consented", "Re-run `gcloud auth application-default login` and tick the "
     "Google Cloud Platform consent checkbox."),
    ("Reauthentication", "Credentials expired: run `gcloud auth application-default login`."),
    ("DefaultCredentialsError", "No ADC found: run `gcloud auth application-default login`."),
    ("invalid_grant", "Credentials expired: run `gcloud auth application-default login`."),
    ("AGENT_RUNTIME_NAME not found", "No runtime configured: run `agent-sandbox runtime create` or set "
     "AGENT_RUNTIME_NAME in .env."),
    ("Project ID not found", "Set GOOGLE_CLOUD_PROJECT (and GOOGLE_CLOUD_LOCATION) in .env or the environment."),
    ("PERMISSION_DENIED", "The caller needs roles/aiplatform.user on the workload project (and KMS access for CMEK)."),
    ("NOT_FOUND", "The sandbox expired or was deleted. Start a new one with `agent-sandbox start`."),
    ("INTERNAL", "Shell/container sandboxes need compute + artifactregistry APIs and often fail under CMEK in "
     "preview regions; prefer the code sandbox (`--kind code`)."),
]


def hint_for(error: BaseException) -> Optional[str]:
    text = f"{type(error).__name__}: {error}"
    for needle, hint in HINTS:
        if needle in text:
            return hint
    return None


# --------------------------------------------------------------------------- output
def _use_json(args: argparse.Namespace) -> bool:
    fmt = getattr(args, "format", "auto")
    return fmt == "json" or (fmt == "auto" and not sys.stdout.isatty())


def emit(args: argparse.Namespace, payload: Dict[str, Any], text: Optional[str] = None) -> None:
    if _use_json(args):
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(text if text is not None else json.dumps(payload, indent=2, default=str))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- helpers
def _client_and_runtime() -> Tuple[Any, str]:
    from .client import get_agent_client, get_runtime_name

    return get_agent_client(), get_runtime_name()


_CLIENTS: Dict[Tuple[str, str], Any] = {}


def _client_for_runtime(runtime: str) -> Any:
    """Client for the project/region a runtime lives in (sandboxes may belong to other projects)."""
    match = re.match(r"projects/([^/]+)/locations/([^/]+)/", runtime)
    if not match:
        return _client_and_runtime()[0]
    key = (match.group(1), match.group(2))
    if key not in _CLIENTS:
        from .client import get_agent_client

        _CLIENTS[key] = get_agent_client(project=key[0], location=key[1])
    return _CLIENTS[key]


def _sandbox_class(kind: str):
    if kind == "shell":
        from .shell import ShellSandbox

        return ShellSandbox
    from .code_execution import CodeExecutionSandbox

    return CodeExecutionSandbox


def _attach(ref: Optional[str], store: StateStore) -> Any:
    tracked = store.resolve(ref)
    return _sandbox_class(tracked.kind).attach(
        tracked.name, runtime_name=tracked.runtime, client=_client_for_runtime(tracked.runtime), tracker=store
    )


def _install_signal_handlers() -> None:
    """Turn SIGTERM/SIGHUP into SystemExit so ``with`` blocks still delete the sandbox."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: sys.exit(128 + signum))


def _read_code(args: argparse.Namespace) -> str:
    sources = [s for s in (args.code, args.file) if s is not None]
    if args.stdin:
        sources.append(sys.stdin.read())
    if len(sources) != 1:
        raise UsageError("Provide exactly one of --code, --file or --stdin.")
    if args.file is not None:
        return Path(args.file).read_text(encoding="utf-8")
    return sources[0]


def _parse_uploads(specs: List[str]) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    for spec in specs or []:
        local, _, remote = spec.partition(":")
        path = Path(local).expanduser()
        if not path.is_file():
            raise UsageError(f"Upload source is not a file: {local}")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise UsageError(f"{local} exceeds the 100 MB per-request limit.")
        entry: Dict[str, Any] = {"name": remote or path.name, "content": path.read_bytes()}
        mime = mimetypes.guess_type(entry["name"])[0]
        if mime:
            entry["mimeType"] = mime
        files.append(entry)
    return files


class UsageError(Exception):
    pass


def _finish_exec(args: argparse.Namespace, result: Any, extra: Dict[str, Any]) -> int:
    """Save returned files, truncate big stdout, print, and compute the exit code."""
    from .results import save_files

    out_dir = Path(args.out).expanduser()
    saved = save_files(result.files, out_dir) if result.files else []
    payload = result.to_dict({f.name: str(p) for f, p in zip(result.files, saved)})
    limit = args.max_output_chars
    if limit and len(result.stdout) > limit:
        out_dir.mkdir(parents=True, exist_ok=True)
        full = out_dir / "_stdout.txt"
        full.write_text(result.stdout, encoding="utf-8")
        payload["stdout"] = result.stdout[:limit]
        payload["stdout_truncated"] = True
        payload["stdout_full_path"] = str(full)
    payload.update(extra)

    lines = []
    if payload["stdout"]:
        lines.append(payload["stdout"].rstrip("\n"))
    if payload["stderr"]:
        lines.append(f"[stderr]\n{payload['stderr'].rstrip()}")
    for f in payload["files"]:
        lines.append(f"[file] {f['name']} ({f['bytes']} bytes) -> {f['saved_to']}")
    lines.append(f"[exit {payload['exit_status']}]")
    emit(args, payload, "\n".join(lines))
    return EXIT_OK if result.ok else EXIT_REMOTE_FAILED


# --------------------------------------------------------------------------- commands
def cmd_doctor(args: argparse.Namespace) -> int:
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "", hint: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "hint": hint})

    from .client import load_environment

    load_environment()
    for var, why in [
        ("GOOGLE_CLOUD_PROJECT", "workload project id"),
        ("GOOGLE_CLOUD_LOCATION", "region, e.g. europe-west8"),
        ("KMS_KEY_NAME", "CryptoKey used for CMEK (needed to create a runtime)"),
        ("AGENT_RUNTIME_NAME", "parent runtime; created by `agent-sandbox runtime create`"),
    ]:
        value = os.environ.get(var)
        check(f"env:{var}", bool(value), value or "", "" if value else f"Set {var} ({why}) in .env.")

    try:
        import google.auth
        import google.auth.transport.requests

        creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        check("adc", True, f"credentials valid (project hint: {project})")
    except Exception as exc:  # noqa: BLE001
        check("adc", False, str(exc)[:200], hint_for(exc) or "Run `gcloud auth application-default login`.")

    client = runtime = None
    try:
        client, runtime = _client_and_runtime()
        check("sdk", True, "agentplatform client initialised")
    except Exception as exc:  # noqa: BLE001
        check("sdk", False, str(exc)[:200], hint_for(exc) or "")

    if client and runtime:
        try:
            info = client.runtimes.get(name=runtime).api_resource
            key = getattr(info.encryption_spec, "kms_key_name", None) if info.encryption_spec else None
            check("runtime", True, f"{info.name} display_name={info.display_name}")
            check("runtime:cmek", bool(key), key or "no encryption_spec",
                  "" if key else "Runtime has no CMEK key; the org policy will reject it. Recreate with CMEK.")
        except Exception as exc:  # noqa: BLE001
            check("runtime", False, str(exc)[:200], hint_for(exc) or "")
        try:
            live = list(client.sandboxes.list(name=runtime))
            check("sandboxes:list", True, f"{len(live)} sandbox(es) currently exist in the runtime")
        except Exception as exc:  # noqa: BLE001
            check("sandboxes:list", False, str(exc)[:200], hint_for(exc) or "")

    if args.live and client and runtime:
        started = time.time()
        try:
            from .code_execution import CodeExecutionSandbox

            with CodeExecutionSandbox(client=client, runtime_name=runtime, ttl="120s",
                                      display_name=f"{DISPLAY_NAME_PREFIX}doctor",
                                      tracker=StateStore()) as sandbox:
                result = sandbox.run("print(6*7)")
            ok = result.ok and "42" in result.stdout
            check("live-smoke-test", ok, f"create+exec+delete in {time.time() - started:.1f}s")
        except Exception as exc:  # noqa: BLE001
            check("live-smoke-test", False, str(exc)[:200], hint_for(exc) or "")

    ok = all(c["ok"] for c in checks)
    text = "\n".join(
        f"[{'ok' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}" + (f"\n       -> {c['hint']}" if c["hint"] else "")
        for c in checks
    )
    emit(args, {"ok": ok, "checks": checks}, text)
    return EXIT_OK if ok else EXIT_ERROR


def cmd_start(args: argparse.Namespace) -> int:
    ttl = parse_ttl(args.ttl)
    client, runtime = _client_and_runtime()
    store = StateStore()
    cls = _sandbox_class(args.kind)
    kwargs: Dict[str, Any] = {}
    if args.kind == "shell" and args.template:
        kwargs["template_name"] = args.template
    sandbox = cls(
        runtime_name=runtime,
        client=client,
        ttl=ttl,
        display_name=f"{DISPLAY_NAME_PREFIX}{args.name or args.kind}-{int(time.time()) % 100000}",
        tracker=store,
        **kwargs,
    )
    sandbox.start()
    expires = time.time() + ttl
    payload = {
        "ok": True,
        "sandbox": sandbox.sandbox_name,
        "id": sandbox.sandbox_name.rsplit("/", 1)[-1],
        "kind": args.kind,
        "ttl_seconds": ttl,
        "expires_at": iso(expires),
        "billing": BILLING_NOTE,
    }
    emit(args, payload, f"Started {args.kind} sandbox {payload['id']} (auto-expires {payload['expires_at']}).\n{BILLING_NOTE}")
    return EXIT_OK


def cmd_exec(args: argparse.Namespace) -> int:
    code = _read_code(args)
    files = _parse_uploads(args.upload)
    store = StateStore()
    sandbox = _attach(args.sandbox, store)
    if sandbox.kind != "code":
        raise UsageError("`exec` runs Python on a code sandbox; use `bash` for shell sandboxes.")
    started = time.time()
    result = sandbox.run(code, files=files or None)
    return _finish_exec(args, result, {"sandbox": sandbox.sandbox_name, "elapsed_s": round(time.time() - started, 2)})


def cmd_bash(args: argparse.Namespace) -> int:
    store = StateStore()
    sandbox = _attach(args.sandbox, store)
    started = time.time()
    kwargs: Dict[str, Any] = {"timeout": args.timeout}
    if args.cwd:
        kwargs["cwd"] = args.cwd
    result = sandbox.run_bash(args.command, **kwargs)
    return _finish_exec(args, result, {"sandbox": sandbox.sandbox_name, "elapsed_s": round(time.time() - started, 2)})


def cmd_run(args: argparse.Namespace) -> int:
    """One-shot: create -> (upload) -> execute -> save outputs -> delete, always."""
    if args.bash is not None and (args.code is not None or args.file is not None or args.stdin):
        raise UsageError("Use --bash OR one of --code/--file/--stdin.")
    code = None if args.bash is not None else _read_code(args)
    files = _parse_uploads(args.upload)
    ttl = parse_ttl(args.ttl)
    client, runtime = _client_and_runtime()
    _install_signal_handlers()
    started = time.time()
    from .code_execution import CodeExecutionSandbox

    with CodeExecutionSandbox(
        runtime_name=runtime,
        client=client,
        ttl=ttl,
        display_name=f"{DISPLAY_NAME_PREFIX}run-{int(started) % 100000}",
        tracker=StateStore(),
    ) as sandbox:
        if args.bash is not None:
            if files:  # attach files with a no-op call first so bash can see them
                sandbox.run("pass", files=files)
            result = sandbox.run_bash(args.bash, cwd=args.cwd, timeout=args.timeout)
        else:
            result = sandbox.run(code, files=files or None)
        name = sandbox.sandbox_name
    return _finish_exec(
        args, result,
        {"sandbox": name, "sandbox_deleted": True, "elapsed_s": round(time.time() - started, 2)},
    )


def cmd_upload(args: argparse.Namespace) -> int:
    local = Path(args.local).expanduser()
    if not local.is_file():
        raise UsageError(f"Not a file: {local}")
    if local.stat().st_size > MAX_UPLOAD_BYTES:
        raise UsageError("File exceeds the 100 MB per-request limit.")
    sandbox = _attach(args.sandbox, StateStore())
    remote = args.remote or local.name
    sandbox.put(remote, local.read_bytes())
    emit(args, {"ok": True, "uploaded": remote, "bytes": local.stat().st_size},
         f"Uploaded {local} -> {remote}")
    return EXIT_OK


def cmd_download(args: argparse.Namespace) -> int:
    sandbox = _attach(args.sandbox, StateStore())
    item = sandbox.fetch(args.remote)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = safe_target(out_dir, item.name)
    target.write_bytes(item.data)
    emit(args, {"ok": True, "remote": args.remote, "saved_to": str(target), "bytes": len(item.data)},
         f"Downloaded {args.remote} -> {target} ({len(item.data)} bytes)")
    return EXIT_OK


def _list_live(client: Any, runtime: str) -> List[Any]:
    return list(client.sandboxes.list(name=runtime))


def cmd_status(args: argparse.Namespace) -> int:
    store = StateStore()
    tracked = store.list()
    if args.brief and not tracked:
        # Cheap path for the SessionStart hook: nothing tracked, nothing to warn about.
        return EXIT_OK

    # Look at the configured runtime plus every runtime we ever started a sandbox in
    # (project-scoped setups can span several).
    runtimes: List[str] = []
    try:
        runtimes.append(_client_and_runtime()[1])
    except Exception:  # noqa: BLE001 - unconfigured here is fine if the ledger has entries
        if not tracked:
            if args.brief:
                return EXIT_OK
            raise
    runtimes += [t.runtime for t in tracked if t.runtime not in runtimes]

    live_by_name: Dict[str, Any] = {}
    listed, errors = set(), {}
    for runtime in dict.fromkeys(runtimes):
        try:
            for sandbox in _list_live(_client_for_runtime(runtime), runtime):
                live_by_name[sandbox.name] = sandbox
            listed.add(runtime)
        except Exception as exc:  # noqa: BLE001
            errors[runtime] = f"{type(exc).__name__}: {exc}"
    if errors and not listed:
        if args.brief:
            return EXIT_OK  # never break session start
        raise RuntimeError(next(iter(errors.values())))

    now = time.time()
    rows, pruned = [], []
    tracked_by_name = {t.name: t for t in tracked}
    for t in tracked:
        if t.runtime in listed and t.name not in live_by_name:
            store.remove(t.name)  # gone server-side (deleted or TTL expired)
            pruned.append(t.name)
    for name, s in live_by_name.items():
        t = tracked_by_name.get(name)
        expire = s.expire_time.timestamp() if getattr(s, "expire_time", None) else None
        rows.append({
            "id": name.rsplit("/", 1)[-1],
            "name": name,
            "runtime": name.split("/sandboxEnvironments/")[0],
            "state": str(s.state).rsplit(".", 1)[-1],
            "kind": t.kind if t else "unknown",
            "display_name": s.display_name,
            "tracked": t is not None,
            "project_dir": t.project_dir if t else None,
            "age_seconds": int(now - s.create_time.timestamp()) if getattr(s, "create_time", None) else None,
            "expires_at": iso(expire) if expire else None,
            "remaining_seconds": int(expire - now) if expire else None,
        })

    if args.brief:
        if rows:
            ids = ", ".join(r["id"] for r in rows)
            print(f"WARNING: {len(rows)} Agent Platform sandbox(es) still running and billed per second: "
                  f"{ids}. Stop with `agent-sandbox cleanup` (yours) or `agent-sandbox stop <id>`.")
        return EXIT_OK

    text = "\n".join(
        f"{r['id']}  {r['state']:<14} {r['kind']:<7} tracked={r['tracked']!s:<5} "
        f"age={r['age_seconds']}s remaining={r['remaining_seconds']}s"
        for r in rows
    ) or "No running sandboxes."
    if errors:
        text += "\n" + "\n".join(f"[could not list {rt}: {err}]" for rt, err in errors.items())
    emit(args, {"ok": not errors, "running": len(rows), "sandboxes": rows, "pruned_stale_ledger_entries": pruned,
                "current": store.current(), "errors": errors}, text)
    return EXIT_OK if not errors else EXIT_ERROR


def _delete_tracked(entries: List[TrackedSandbox], store: StateStore) -> Tuple[List[str], List[str]]:
    """Delete each entry using a client for *its own* runtime's project/region."""
    deleted, failed = [], []
    for t in entries:
        try:
            client = _client_for_runtime(t.runtime)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: cannot reach {t.runtime}: {exc}", file=sys.stderr)
            failed.append(t.name)
            continue
        sandbox = _sandbox_class(t.kind).attach(t.name, runtime_name=t.runtime, client=client, tracker=store)
        (deleted if sandbox.close() else failed).append(t.name)
    return deleted, failed


def cmd_stop(args: argparse.Namespace) -> int:
    store = StateStore()
    if args.all:
        entries = store.list()
    elif args.sandbox and args.sandbox.startswith("projects/") and args.sandbox not in {t.name for t in store.list()}:
        # Explicit full resource name that we did not create: delete it only because the user named it.
        from .base import BaseSandbox

        runtime = args.sandbox.split("/sandboxEnvironments/")[0]
        ok = BaseSandbox.attach(args.sandbox, runtime_name=runtime, client=_client_for_runtime(runtime)).close()
        emit(args, {"ok": ok, "deleted": [args.sandbox] if ok else [], "failed": [] if ok else [args.sandbox]},
             f"{'Deleted' if ok else 'FAILED to delete'} {args.sandbox}")
        return EXIT_OK if ok else EXIT_ERROR
    else:
        entries = [store.resolve(args.sandbox)]
    if not entries:
        emit(args, {"ok": True, "deleted": [], "failed": []}, "Nothing to stop.")
        return EXIT_OK
    deleted, failed = _delete_tracked(entries, store)
    emit(args, {"ok": not failed, "deleted": deleted, "failed": failed},
         f"Deleted {len(deleted)} sandbox(es)." + (f" FAILED: {failed}" if failed else ""))
    return EXIT_OK if not failed else EXIT_ERROR


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Delete every sandbox this machine started (and optionally leftovers). Hook-friendly."""
    if args.everything and not args.yes:
        raise UsageError("--everything deletes sandboxes created by anyone; add --yes to confirm.")
    store = StateStore()
    entries = store.list()
    if args.here:
        from .client import project_root

        here = str(project_root())
        entries = [t for t in entries if t.project_dir == here]
    if not entries and not (args.orphans or args.everything):
        if not args.quiet:
            emit(args, {"ok": True, "deleted": [], "failed": []}, "Nothing to clean up.")
        return EXIT_OK

    deleted, failed = _delete_tracked(entries, store)

    if args.orphans or args.everything:
        client, runtime = _client_and_runtime()
        known = {t.name for t in entries}
        for s in _list_live(client, runtime):
            if s.name in known:
                continue
            if args.everything or (s.display_name or "").startswith(DISPLAY_NAME_PREFIX):
                from .base import BaseSandbox

                ok = BaseSandbox.attach(s.name, runtime_name=runtime, client=client).close()
                (deleted if ok else failed).append(s.name)

    if deleted or failed or not args.quiet:
        emit(args, {"ok": not failed, "deleted": deleted, "failed": failed},
             f"Deleted {len(deleted)} sandbox(es)." + (f" FAILED: {failed}" if failed else ""))
    return EXIT_OK if not failed else EXIT_ERROR


def cmd_runtime(args: argparse.Namespace) -> int:
    if args.action == "create":
        from .runtime import create_cmek_runtime

        name = create_cmek_runtime(display_name=args.display_name)
        emit(args, {"ok": True, "runtime": name, "saved": "AGENT_RUNTIME_NAME written to the env file"},
             f"Created CMEK runtime:\n  {name}\nAGENT_RUNTIME_NAME saved to the env file.")
        return EXIT_OK
    from .client import get_runtime_name
    from .runtime import get_runtime_info

    info = get_runtime_info(get_runtime_name())
    info["encryption_spec"] = str(info["encryption_spec"])
    emit(args, {"ok": True, **info}, "\n".join(f"{k}: {v}" for k, v in info.items()))
    return EXIT_OK


def cmd_templates(args: argparse.Namespace) -> int:
    client, runtime = _client_and_runtime()
    if args.action == "create":
        from .client import persist_env_var
        from .shell import create_shell_template

        name = create_shell_template(display_name=args.display_name, runtime_name=runtime, client=client)
        persist_env_var("SANDBOX_TEMPLATE_NAME", name)
        emit(args, {"ok": True, "template": name}, f"Created template:\n  {name}\nSANDBOX_TEMPLATE_NAME saved.")
        return EXIT_OK
    items = [{"name": t.name, "state": str(t.state)} for t in client.sandboxes.templates.list(name=runtime)]
    emit(args, {"ok": True, "templates": items}, "\n".join(f"{i['name']} [{i['state']}]" for i in items) or "No templates.")
    return EXIT_OK


def cmd_hooks(args: argparse.Namespace) -> int:
    if args.action == "print":
        print(json.dumps(hooks_mod.snippet(), indent=2))
        return EXIT_OK
    path = hooks_mod.install(args.scope, uninstall=args.uninstall)
    verb = "Removed hooks from" if args.uninstall else "Installed SessionStart/SessionEnd hooks in"
    emit(args, {"ok": True, "settings": str(path), "uninstalled": args.uninstall}, f"{verb} {path}")
    return EXIT_OK


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip("\"' ")
    return values


def cmd_config(args: argparse.Namespace) -> int:
    """Show or write config. Scopes: project (.agent-sandbox.env in the repo) or user (~/.config)."""
    from .client import (CONFIG_KEYS, default_env_path, env_search_paths, load_environment,
                         persist_env_var, project_root)

    if args.action in ("set", "init"):
        path = default_env_path(args.scope) if args.scope else default_env_path()
        if args.action == "set":
            pairs = {}
            for pair in args.values:
                key, sep, value = pair.partition("=")
                if not sep or not key.isidentifier():
                    raise UsageError(f"Expected KEY=VALUE, got {pair!r}")
                pairs[key] = value
        else:
            if not args.project:
                raise UsageError("--project is required")
            kms_project = args.kms_project or args.project
            key_name = (f"projects/{kms_project}/locations/{args.location}/keyRings/{args.key_ring}"
                        f"/cryptoKeys/{args.key_name}")
            pairs = {
                "PROJECT_ID": args.project, "KMS_PROJECT_ID": kms_project, "LOCATION": args.location,
                "KEY_RING": args.key_ring, "KEY_NAME": args.key_name,
                "GOOGLE_CLOUD_PROJECT": args.project, "GOOGLE_CLOUD_LOCATION": args.location,
                "KMS_KEY_NAME": key_name,
            }
        for k, v in pairs.items():
            persist_env_var(k, v, path)
        emit(args, {"ok": True, "file": str(path), "set": sorted(pairs)}, f"Updated {path}: {', '.join(sorted(pairs))}")
        return EXIT_OK

    load_environment()
    sources: Dict[str, Optional[str]] = {}
    parsed = [(p, _parse_env_file(p)) for p in env_search_paths()]
    for key in CONFIG_KEYS:
        sources[key] = next((str(p) for p, vals in parsed if key in vals), None)
    values = {k: os.environ.get(k) for k in CONFIG_KEYS}
    files = [{"path": str(p), "exists": p.exists()} for p, _ in parsed]
    text = "\n".join(f"{k}={v or '(unset)'}" + (f"   <- {sources[k]}" if sources[k] else "") for k, v in values.items())
    text += "\nSearched (highest priority first; * = exists):\n" + "\n".join(
        f"  {'*' if f['exists'] else ' '} {f['path']}" for f in files)
    emit(args, {"ok": True, "values": values, "sources": sources, "files": files,
                "write_target": str(default_env_path()), "project_root": str(project_root())}, text)
    return EXIT_OK


def cmd_repl(args: argparse.Namespace) -> int:
    from .repl import main as repl_main

    repl_main(["--ttl", args.ttl])
    return EXIT_OK


# --------------------------------------------------------------------------- parser
def _add_exec_input(p: argparse.ArgumentParser) -> None:
    p.add_argument("--code", help="Python source to execute")
    p.add_argument("--file", help="Path to a local Python script to execute")
    p.add_argument("--stdin", action="store_true", help="Read the Python source from stdin")
    p.add_argument("--upload", action="append", default=[], metavar="LOCAL[:REMOTE]",
                   help="Upload a local file with the call (repeatable; max 100 MB each)")


def _add_result_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default="sandbox-results", help="Directory for files the sandbox returns")
    p.add_argument("--max-output-chars", type=int, default=20000,
                   help="Truncate stdout beyond this (full text saved to <out>/_stdout.txt); 0 = no limit")


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("agent-sandbox")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-sandbox",
        description="Run code in Google Cloud Agent Platform sandboxes (CMEK). Billed per second: "
                    "always stop what you start.",
    )
    fmt_help = "Output format (auto: json when piped, text on a terminal)"
    parser.add_argument("--format", choices=["auto", "json", "text"], default="auto", help=fmt_help)
    parser.add_argument("--version", action="version", version=f"agent-sandbox {_version()}")
    # Same flag on every subcommand so it works before or after the command name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["auto", "json", "text"], default=argparse.SUPPRESS, help=fmt_help)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("doctor", parents=[common], help="Check env, credentials, runtime/CMEK and (optionally) run a live smoke test")
    p.add_argument("--live", action="store_true", help="Create a sandbox, run print(6*7), delete it (a few seconds billed)")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", parents=[common], help="One-shot: create, execute, save outputs, ALWAYS delete (preferred)")
    _add_exec_input(p)
    p.add_argument("--bash", help="Run this bash command instead of Python")
    p.add_argument("--cwd", help="Working directory for --bash")
    p.add_argument("--timeout", type=int, default=120, help="Timeout in seconds for --bash")
    p.add_argument("--ttl", default=f"{default_ttl_seconds()}s", help="Server-side expiry backstop (default 900s, max 3600s)")
    _add_result_opts(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("start", parents=[common], help="Create a long-lived sandbox (you must `stop` it; billed per second)")
    p.add_argument("--kind", choices=["code", "shell"], default="code")
    p.add_argument("--ttl", default=f"{default_ttl_seconds()}s")
    p.add_argument("--name", help="Label added to the display name")
    p.add_argument("--template", help="Shell template resource name (default: $SANDBOX_TEMPLATE_NAME)")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("exec", parents=[common], help="Run Python in a started code sandbox")
    p.add_argument("--sandbox", help="Sandbox id or name (default: the current one)")
    _add_exec_input(p)
    _add_result_opts(p)
    p.set_defaults(func=cmd_exec)

    p = sub.add_parser("bash", parents=[common], help="Run a bash command in a started sandbox")
    p.add_argument("command", help="Command line, e.g. 'ls -la'")
    p.add_argument("--sandbox")
    p.add_argument("--cwd")
    p.add_argument("--timeout", type=int, default=60)
    _add_result_opts(p)
    p.set_defaults(func=cmd_bash)

    p = sub.add_parser("upload", parents=[common], help="Upload one local file to a started sandbox")
    p.add_argument("local")
    p.add_argument("remote", nargs="?", help="Remote path (default: basename in the sandbox home)")
    p.add_argument("--sandbox")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("download", parents=[common], help="Download a file (or a directory as .tar.gz) from a started sandbox")
    p.add_argument("remote")
    p.add_argument("--out", default="sandbox-results")
    p.add_argument("--sandbox")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("status", parents=[common], help="List running sandboxes with age and remaining TTL")
    p.add_argument("--brief", action="store_true", help="Print a one-line warning only if something is running (for hooks)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stop", parents=[common], help="Delete a sandbox (default: the current one)")
    p.add_argument("sandbox", nargs="?", help="Id, name, or full resource name")
    p.add_argument("--all", action="store_true", help="Delete every sandbox started from this machine")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("cleanup", parents=[common], help="Delete every sandbox this machine started (use at end of work / in hooks)")
    p.add_argument("--orphans", action="store_true", help=f"Also delete untracked '{DISPLAY_NAME_PREFIX}*' sandboxes in the runtime")
    p.add_argument("--everything", action="store_true", help="Delete ALL sandboxes in the runtime (needs --yes)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--here", action="store_true",
                   help="Only sandboxes started from this git project (for parallel sessions in different repos)")
    p.add_argument("--quiet", action="store_true", help="Print nothing when there was nothing to do")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("runtime", parents=[common], help="Create or inspect the CMEK parent runtime")
    p.add_argument("action", choices=["create", "info"])
    p.add_argument("--display-name", default="sandbox-cmek-runtime")
    p.set_defaults(func=cmd_runtime)

    p = sub.add_parser("templates", parents=[common], help="Create or list shell sandbox templates")
    p.add_argument("action", choices=["create", "list"])
    p.add_argument("--display-name", default="shell-sandbox-template")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("hooks", parents=[common], help="Print or install Claude Code hooks that stop sandboxes when a session ends")
    p.add_argument("action", choices=["print", "install"])
    p.add_argument("--scope", choices=["user", "project", "local"], default="user")
    p.add_argument("--uninstall", action="store_true")
    p.set_defaults(func=cmd_hooks)

    p = sub.add_parser("config", parents=[common], help="Show or write the per-user config (~/.config/agent-sandbox/env)")
    p.add_argument("action", choices=["show", "set", "init"])
    p.add_argument("values", nargs="*", metavar="KEY=VALUE", help="for `set`")
    p.add_argument("--scope", choices=["project", "user"],
                   help="project: <git root>/.agent-sandbox.env (this repo's own sandbox); user: ~/.config/agent-sandbox/env")
    p.add_argument("--project", help="init: workload project id")
    p.add_argument("--location", default="europe-west8", help="init: region (key and runtime must match)")
    p.add_argument("--key-ring", default="agent-engine")
    p.add_argument("--key-name", default="agent-engine-key")
    p.add_argument("--kms-project", help="init: project holding the key (default: --project)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("repl", parents=[common], help="Interactive shell/Python session in a fresh sandbox (humans)")
    p.add_argument("--ttl", default="1800s")
    p.set_defaults(func=cmd_repl)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        emit(args, {"ok": False, "error": str(exc)}, f"Error: {exc}")
        return EXIT_USAGE
    except LookupError as exc:
        emit(args, {"ok": False, "error": str(exc)}, f"Error: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - surface every failure as structured output
        hint = hint_for(exc)
        emit(args, {"ok": False, "error": f"{type(exc).__name__}: {exc}", "hint": hint},
             f"Error: {type(exc).__name__}: {exc}" + (f"\nHint: {hint}" if hint else ""))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
