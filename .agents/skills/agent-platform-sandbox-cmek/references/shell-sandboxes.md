# Shell / container sandboxes

The **code sandbox** (`agent-sandbox start --kind code`, the default) is the verified path under CMEK and
also runs bash through `agent-sandbox bash`. Use a **shell sandbox** only when you need a real container
shell, a custom image, or behaviour the code sandbox lacks.

## Status under CMEK

In Preview regions such as `europe-west8`, the default shell container
(`DEFAULT_CONTAINER_CATEGORY_SHELL_SANDBOX`) may fail with `code: 13, "INTERNAL"` because pre-warmed Google
images are not available with local-key encryption. This was not verified end to end in this repository.
Alternatives, in order of preference:

1. Stay on the code sandbox (`agent-sandbox bash '...'` runs real bash there).
2. Custom container image in Artifact Registry (`custom_container_environment`).
3. Retry the default shell template after enabling the auxiliary APIs.

## Prerequisites

```bash
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com --project="$PROJECT_ID"
agent-sandbox templates create     # once; saves SANDBOX_TEMPLATE_NAME to .env; reuse the exact name returned
agent-sandbox templates list
```

Without an explicit template the service may create a fresh default template on **every** `create()`, and
deleting a sandbox does not delete its template. A template that sandboxes still use cannot be deleted.

## Usage

```bash
agent-sandbox start --kind shell --ttl 15m
agent-sandbox bash 'mkdir -p input output && echo hi > input/a.txt'
agent-sandbox upload ./report.csv input/report.csv          # base64 over bash: small files only
agent-sandbox bash 'tr a-z A-Z < input/a.txt > output/a.txt'
agent-sandbox download output/a.txt --out ./sandbox-results
agent-sandbox stop
```

`exec` (Python) is not available on shell sandboxes; run `python3 script.py` through `bash` after uploading
the script and checking that the image has an interpreter.

## Behaviour

* Commands run as unprivileged `appuser`, without `sudo`; working dir defaults to `/workspace`.
* Every call is a fresh shell: `cd`/`export` do not persist; use `--cwd` and files under `/workspace`.
* The filesystem persists for the sandbox's life. Outbound internet is disabled unless the template enables
  it, so do not assume `pip install` or downloads work.
* The container does not inherit your ADC; large-file transfer needs explicit storage integration.
* `--timeout` defaults to 60 s per command.
