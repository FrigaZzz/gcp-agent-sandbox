# SDK contracts (verified against `google-cloud-aiplatform[agent_engines]==2.3.0`, `v1beta1`)

Use these when writing Python against `agentplatform` directly instead of the CLI/library. Everything
marked *observed* was checked against a live CMEK runtime in `europe-west8`; the API is Preview and may change.

```python
import agentplatform
client = agentplatform.Client(project=P, location=L, http_options={"api_version": "v1beta1"})
```

## Resources

* `client.runtimes.create(config={"display_name", "encryption_spec": {"kms_key_name"}})` → object with
  `.api_resource.name` / `.api_resource.encryption_spec`.
* `client.sandboxes.create(name=<runtime>, spec=…, config={"display_name","ttl":"900s","wait_for_completion":True})`
  → operation; check `.error`, use `.response.name`. Code sandbox spec:
  `{"code_execution_environment": {"code_language": "LANGUAGE_PYTHON"}}`; shell spec: `{"shell_environment": {}}`
  (+ `config["sandbox_environment_template"]`).
* `sandboxes.get / list / delete`, `sandboxes.templates.create / list / delete`. `list()` returns an iterator.
  `get()` returns `state`, `create_time`, `expire_time`, `display_name` (`ttl` is `None`). After delete,
  `get()` raises `404 NOT_FOUND`. *observed*
* Other methods exist (`pause`, `resume`, `snapshots`, `generate_access_token`); not used by this repo.

## `execute_code(name=…, input_data={"code": str, "files": [{"name", "content": bytes, "mimeType"?}]})`

Returns `response.outputs`, a list of chunks *(observed)*:

| Chunk | Identify by | Content |
|---|---|---|
| Result | `mime_type == "application/json"` | `{"exit_status_int": 0, "msg_out": "stdout", "msg_err": "stderr"}` |
| File | `chunk.metadata.attributes["file_name"]` (bytes) | `chunk.data` = raw bytes; `mime_type` may be `text/plain`, `application/octet-stream` or `None` |

Behaviour *(observed)*:

* Working directory `/home/bard`, user `root`, Python 3.12, numpy/pandas/matplotlib present, no DNS/internet.
* Uploaded files persist for the sandbox's lifetime and are visible to later calls; nested names
  (`a/b/c.csv`) create directories. Uploaded files are **not** echoed back.
* Only files **created** during the call are returned, in the cwd tree, names with subdirectories
  (`sub/deep.txt`). `touch` or rewriting identical content does not return a file; copying to a new name does.
  Dot-directories, `/tmp` and paths outside the cwd are not returned. A 20 MB file returned fine; the documented
  limit is 100 MB per request or response.
* Uncaught Python exception → `exit_status_int` 106, traceback in `msg_err`; the SDK does **not** raise.
  Writing to stderr alone leaves `exit_status_int` 0, so test the status, not `msg_err`.
* A text/plain *file* chunk is a file, not stdout: always check `file_name` first.
* Warm create took ~2 s; the docs quote ~2 min for a cold start.
* Do not base64-decode `chunk.data`. (Some doc examples use a different JSON shape with base64 `content`.)

## `execute_bash(name=…, command=…, cwd="/workspace", timeout=60)` (shell sandboxes only)

Returns a dict: `stdout`, `stderr`, `returncode`, `duration_ms`. A non-zero `returncode` is not an exception.
Each call starts a fresh shell (no `cd`/`export` persistence); the filesystem persists. Commands run as an
unprivileged user without `sudo`; egress is off unless the template enables it. Files move as base64
through the command/stdout, so only small files.

## Download pattern for code sandboxes

Because only newly created files are returned, `fetch()` copies the target into a fresh visible scratch
directory (`asbx_dl_<hex>/`), returns that copy, and deletes the scratch directory in a second call.
Directories are archived with `shutil.make_archive(..., "gztar")`.
