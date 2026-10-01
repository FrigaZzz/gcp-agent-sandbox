# First-time setup: Agent Platform Sandboxes with CMEK

Goal: a parent **Agent Runtime** (formerly Agent Engine) protected by a Cloud KMS key, so sandboxes can be
created under it. No LLM agent needs to be deployed; a bare runtime is enough. Skip this file if
`agent-sandbox doctor` already passes.

## Why CMEK is mandatory here

Organisations that enforce `constraints/gcp.restrictNonCmekServices` reject runtime creation without a key:
`400 FAILED_PRECONDITION`. The key is attached **once, when the parent runtime is created**; every sandbox,
template and snapshot under it inherits the encryption. You do not pass a key per sandbox. The key on a
runtime is immutable (new key = new runtime); rotating key *versions* is fine. CMEK covers disk data and
snapshots, not metadata such as names, labels and state.

```text
Project
├── Cloud KMS: key ring + symmetric key, SAME region as the runtime
└── Agent Runtime instance (encryption_spec.kms_key_name = the key)
    ├── Code-execution sandboxes        ← what agent-sandbox uses by default
    ├── Sandbox templates (shell/container)
    └── Snapshots
```

## Who needs which permission

| Operation | Identity | Role / permission |
|---|---|---|
| Enable APIs | Project admin | `serviceusage.services.enable` (e.g. `roles/serviceusage.serviceUsageAdmin`) |
| Create key ring/key, edit key IAM | KMS admin | `roles/cloudkms.admin` on the KMS project (or equivalent custom role) |
| Create/use runtime, sandboxes, templates | You / your app | `roles/aiplatform.user` on the workload project |
| Encrypt/decrypt with the key | **Two Google service agents** (below) | `roles/cloudkms.cryptoKeyEncrypterDecrypter` **on the key only** |

Do not give the application `cloudkms.admin` just to run code: the KMS team can provision the key and hand
over its name.

### The two service agents (both are required)

`<PROJECT_NUMBER>` is the **workload** project's number even when the key lives in a central KMS project.

| Service agent | Needed because |
|---|---|
| `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | Agent Runtime: encrypts sandbox data |
| `service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com` | Core Vertex AI: `runtimes.create` validates this one and fails with `400 INVALID_ARGUMENT … cannot be used in Vertex AI service` if it lacks access. Public docs often mention only the `-re` agent |

Optional, for container/bucket-backed features: `…@compute-system…` and `…@gs-project-accounts…`.

## Steps

All commands use these variables (also the contents of `.env`; copy `.env.example`):

```bash
export PROJECT_ID="my-project"            # workload project
export KMS_PROJECT_ID="$PROJECT_ID"       # or the central KMS project
export LOCATION="europe-west8"            # must be a supported region; key and runtime must match
export KEY_RING="agent-engine"
export KEY_NAME="agent-engine-key"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="$LOCATION"
export KMS_KEY_NAME="projects/${KMS_PROJECT_ID}/locations/${LOCATION}/keyRings/${KEY_RING}/cryptoKeys/${KEY_NAME}"
```

**1. Authenticate** (two separate logins):

```bash
gcloud auth login                                  # for gcloud commands
gcloud auth application-default login              # ADC, used by Python/the CLI; tick the Cloud Platform checkbox
```

**2. Enable APIs and create service identities.** Shell/container sandboxes additionally need Compute and
Artifact Registry (missing → `code: 13 INTERNAL`):

```bash
gcloud services enable aiplatform.googleapis.com cloudkms.googleapis.com \
  compute.googleapis.com artifactregistry.googleapis.com --project="$PROJECT_ID"
gcloud beta services identity create --service=aiplatform.googleapis.com --project="$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
```

`PERMISSION_DENIED` when describing a service agent does not prove it is missing (you may lack read access).
Never hand-create an account with a similar name; ask the cloud team to check provisioning.

**3. Key ring and key** (once; reuse if they exist). Symmetric, purpose `encryption`, same region:

```bash
gcloud kms keyrings create "$KEY_RING" --location="$LOCATION" --project="$KMS_PROJECT_ID"
gcloud kms keys create "$KEY_NAME" --keyring="$KEY_RING" --location="$LOCATION" \
  --purpose=encryption --project="$KMS_PROJECT_ID"
```

Global, multi-region and dual-region keys are not supported. Use the **CryptoKey** name, without
`/cryptoKeyVersions/N`.

**4. Grant both service agents on the key:**

```bash
for SA in "gcp-sa-aiplatform-re" "gcp-sa-aiplatform"; do
  gcloud kms keys add-iam-policy-binding "$KEY_NAME" --keyring="$KEY_RING" --location="$LOCATION" \
    --project="$KMS_PROJECT_ID" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@${SA}.iam.gserviceaccount.com" \
    --role="roles/cloudkms.cryptoKeyEncrypterDecrypter"
done
gcloud kms keys get-iam-policy "$KEY_NAME" --keyring="$KEY_RING" --location="$LOCATION" --project="$KMS_PROJECT_ID"
```

Steps 2–4 are automated by `scripts/setup_kms.sh` (idempotent: it reuses an existing key ring/key).

**5. Create the runtime once** (each call creates a *new* instance; the name is saved to `.env`):

```bash
agent-sandbox runtime create        # or: python scripts/create_instance.py
agent-sandbox runtime info          # confirms encryption_spec.kms_key_name
```

**6. Verify end to end:**

```bash
agent-sandbox doctor --live         # ~3 s of billed sandbox time
```

**7. Install the end-of-session hook** so nothing keeps billing: `references/billing-and-hooks.md`.

## Install the tooling

```bash
uv tool install <repo-root>         # puts `agent-sandbox` on PATH (recommended; needed by hooks)
# or, for development:
python3 -m venv .venv-sandbox && source .venv-sandbox/bin/activate && pip install -e ".[dev]"
```

## Request to send the cloud/KMS team when you cannot create keys

> To enable Agent Platform sandboxes in project `<PROJECT_ID>` (number `<PROJECT_NUMBER>`) we need a
> symmetric CryptoKey in `<LOCATION>`, in a KMS project allowed by our org policies. Runtime creation
> without CMEK is blocked by `constraints/gcp.restrictNonCmekServices`, and the calling identity lacks
> `cloudkms.keyRings.create` / `cloudkms.keyRings.list`. Please grant
> `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key to **both**
> `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` and
> `service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com` (verifying they are provisioned), and
> return the full name `projects/…/locations/<LOCATION>/keyRings/…/cryptoKeys/…`. The application identity
> needs `roles/aiplatform.user`, not KMS administration.

## Cleaning up (outside normal use)

```python
client.sandboxes.delete(name=sandbox_name)                 # or: agent-sandbox stop <id>
client.sandboxes.templates.delete(name=template_name)      # only if no sandbox uses it
client.runtimes.delete(name=runtime_name)                  # the parent; removes everything under it
```

Never destroy the KMS key as demo cleanup: it may protect other resources.
