# Troubleshooting

Start with `agent-sandbox doctor`: its failing check and `hint` usually name the fix. Errors from any
command also carry a `hint` when the message is recognised.

## Setup and permissions

| Symptom (exact text) | Cause | Fix |
|---|---|---|
| `400 FAILED_PRECONDITION` … `gcp.restrictNonCmekServices` | Runtime created without `encryption_spec.kms_key_name`; org policy forces CMEK | `agent-sandbox runtime create` with `KMS_KEY_NAME` set |
| `400 INVALID_ARGUMENT` … `Provided CryptoKey … cannot be used in Vertex AI service … service-<N>@gcp-sa-aiplatform.iam.gserviceaccount.com has Encryption and Decryption access` | `runtimes.create` validates the **core** Vertex AI service agent, which docs often omit | Grant `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key to **both** `gcp-sa-aiplatform` and `gcp-sa-aiplatform-re` agents |
| KMS access denied while provisioning a sandbox | `-re` agent lacks the role, wrong region, or key version disabled | Check key IAM, that key region = runtime region, and the key version is enabled |
| `cloudkms.keyRings.create` / `.list` denied | Caller is not a KMS admin | Ask the KMS team for the key (request template in `setup-cmek.md`) |
| Another org-policy violation | Org restricts which KMS projects are allowed | Use a key from an allowed project |
| `ERROR: (gcloud.auth.application-default.login) … cloud-platform scope is required but not consented` | Consent screen continued without the checkbox | Re-run `gcloud auth application-default login`, tick the Google Cloud Platform box |
| `Reauthentication failed` (gcloud) / `invalid_grant` / `DefaultCredentialsError` | Expired or missing credentials. `gcloud auth login` and ADC are separate | `gcloud auth login` and/or `gcloud auth application-default login` |
| `403 PERMISSION_DENIED` creating a sandbox | Caller lacks `roles/aiplatform.user` on the project | Grant it to the identity that appears in the error |
| `Project ID not found` / `AGENT_RUNTIME_NAME not found` | `.env` not found or incomplete | Set vars or `AGENT_SANDBOX_ENV=/path/to/.env`; run `agent-sandbox runtime create` |
| `vertexai.Client` deprecation warnings | Old namespace | Use `agentplatform.Client` (this repo does) |

## Creating sandboxes

| Symptom | Cause | Fix |
|---|---|---|
| `code: 13, message: "INTERNAL"` on a shell/container template | Compute/Artifact Registry APIs disabled, **or** default shell image unavailable under CMEK in the region (e.g. `europe-west8`) | Enable `compute` + `artifactregistry` APIs; otherwise use the code sandbox, or a custom container image from Artifact Registry. See `shell-sandboxes.md` |
| Create returns before the sandbox is ready | `wait_for_completion` omitted | The CLI always waits; in raw SDK code pass `wait_for_completion=True` and check `operation.error`/`operation.response` |
| `TTL … exceeds the cap` | Requested TTL above `AGENT_SANDBOX_MAX_TTL` (3600 s) | Lower it, or raise the env var deliberately |
| Every shell create makes another template | No explicit template passed | `agent-sandbox templates create` once and reuse `SANDBOX_TEMPLATE_NAME` |

## Running code

| Symptom | Cause | Fix |
|---|---|---|
| Exit code 3, `exit_status` 106, traceback in `stderr` | Your Python raised | Fix the script. 106 is how the platform reports an uncaught Python exception |
| Exit code 3, `exit_status` = N from `--bash` | The command returned N | Read `stderr`; this is the command's own status |
| Script ran but no output file came back | Only files **created under the working directory** during that call are returned. Not returned: uploaded files, files only touched/rewritten, anything in `/tmp` or outside `/home/bard`, files in dot-directories | Write results to a visible path under the cwd; to fetch an older or hidden file use `download` |
| `exec` output lacks a file I created earlier | Files are only returned by the call that created them | Use `download PATH` |
| Output is cut off | stdout > 20,000 chars | Read `stdout_full_path` (`<out>/_stdout.txt`) or raise `--max-output-chars` |
| `ModuleNotFoundError` / `pip install` fails / DNS error | No outbound internet. Preinstalled: numpy, pandas, matplotlib | Use standard library or preinstalled packages; upload needed files |
| `Security: insecure filename in output` | The sandbox returned a path with `..`, absolute path, backslash or a symlink target | Intentional guard: nothing is written. Investigate the code that produced it |
| `404 NOT_FOUND` on `exec`/`download`/`stop` | Sandbox expired (TTL) or was deleted | `agent-sandbox start` again; re-upload files. `status` prunes dead ledger entries |
| `Several sandboxes are tracked and none is current` | Multiple started, current one stopped | Pass `--sandbox <id>` (see `status`) |
| File content is garbled | Treated binary as text | Downloads are raw bytes (`write_bytes`); in the raw SDK never base64-decode `chunk.data` |
| Files vanished | Sandbox deleted/expired | Download before `stop`; sandboxes are not storage |
| `Expected one file for X, got 0` on `download` | Old bug: hidden scratch dir. Fixed; update the package | `git pull && uv tool install --reinstall .` |

## SDK pitfalls (writing your own Python)

| Symptom | Fix |
|---|---|
| `AttributeError: 'Pager' object has no attribute 'sandbox_environment_templates'` | In `google-cloud-aiplatform>=2.3`, `sandboxes.list()` / `templates.list()` return an iterable; loop over it directly |
| `execute_code()` on a shell sandbox does nothing useful | Shell sandboxes use `execute_bash()` |
| Bash failure with no exception | Check `returncode` and `stderr` in the returned dict |
| Some doc examples show `output_files[].content` base64 | That is a different (JSON) format; the chunk format returns raw bytes in `chunk.data` |

## Cost and cleanup

| Symptom | Fix |
|---|---|
| Unsure whether anything is still billing | `agent-sandbox status` (lists everything in the runtime, tracked or not) |
| Leftovers from a crashed session | `agent-sandbox cleanup --orphans` (own `asbx-*` sandboxes) |
| Hook did not stop a sandbox | See "Things to know" in `billing-and-hooks.md`: PATH, expired ADC, hard kill. The TTL removes it by itself |
| Need to wipe the runtime of everything | `agent-sandbox cleanup --everything --yes` (includes other people's sandboxes) |
