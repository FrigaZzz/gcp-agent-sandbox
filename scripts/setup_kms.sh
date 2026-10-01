#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load configuration: same lookup order as the CLI. $AGENT_SANDBOX_ENV, then the nearest
# .agent-sandbox.env (this project's own sandbox), then ~/.config/agent-sandbox/env, then the repo .env.
find_project_env() {
  local dir="$PWD"
  while [ "$dir" != "/" ]; do
    [ -f "$dir/.agent-sandbox.env" ] && { echo "$dir/.agent-sandbox.env"; return; }
    [ "$dir" = "$HOME" ] && return
    dir="$(dirname "$dir")"
  done
}
ENV_FILE=""
for candidate in "${AGENT_SANDBOX_ENV:-}" "$(find_project_env)" "$HOME/.config/agent-sandbox/env" "$REPO_ROOT/.env" "$REPO_ROOT/.env.example"; do
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then ENV_FILE="$candidate"; break; fi
done
[ -n "$ENV_FILE" ] || { echo "No config found. Run: agent-sandbox config init --project <PROJECT_ID>" >&2; exit 1; }
echo "Using config: $ENV_FILE"
source "$ENV_FILE"

echo "=========================================="
echo "Project ID:     $PROJECT_ID"
echo "KMS Project ID: $KMS_PROJECT_ID"
echo "Location:       $LOCATION"
echo "Key Ring:       $KEY_RING"
echo "Key Name:       $KEY_NAME"
echo "=========================================="

echo "[1/5] Retrieving Project Number..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
echo "Project Number: $PROJECT_NUMBER"

echo "[2/5] Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  cloudkms.googleapis.com \
  compute.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$PROJECT_ID" -q

echo "[3/5] Initializing Service Identities..."
gcloud beta services identity create --service=aiplatform.googleapis.com --project="$PROJECT_ID" -q || true
gcloud beta services identity create --service=compute.googleapis.com --project="$PROJECT_ID" -q || true

SERVICE_AGENT_RE="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
SERVICE_AGENT_CORE="service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
SERVICE_AGENT_COMPUTE="service-${PROJECT_NUMBER}@compute-system.iam.gserviceaccount.com"

echo "Agent Runtime Service Agent: $SERVICE_AGENT_RE"
echo "Vertex AI Core Service Agent: $SERVICE_AGENT_CORE"

echo "[4/5] Creating KMS Key Ring and Crypto Key..."
if ! gcloud kms keyrings describe "$KEY_RING" --location="$LOCATION" --project="$KMS_PROJECT_ID" &>/dev/null; then
  echo "Creating key ring: $KEY_RING..."
  gcloud kms keyrings create "$KEY_RING" --location="$LOCATION" --project="$KMS_PROJECT_ID"
else
  echo "Key ring $KEY_RING already exists."
fi

if ! gcloud kms keys describe "$KEY_NAME" --keyring="$KEY_RING" --location="$LOCATION" --project="$KMS_PROJECT_ID" &>/dev/null; then
  echo "Creating crypto key: $KEY_NAME..."
  gcloud kms keys create "$KEY_NAME" \
    --keyring="$KEY_RING" \
    --location="$LOCATION" \
    --purpose=encryption \
    --project="$KMS_PROJECT_ID"
else
  echo "Crypto key $KEY_NAME already exists."
fi

echo "[5/5] Granting Encrypter/Decrypter permissions to Service Agents..."

# 1. Agent Runtime Service Agent (used for sandbox execution)
gcloud kms keys add-iam-policy-binding "$KEY_NAME" \
  --keyring="$KEY_RING" \
  --location="$LOCATION" \
  --project="$KMS_PROJECT_ID" \
  --member="serviceAccount:${SERVICE_AGENT_RE}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" -q

# 2. Vertex AI Core Service Agent (required by client.runtimes.create)
gcloud kms keys add-iam-policy-binding "$KEY_NAME" \
  --keyring="$KEY_RING" \
  --location="$LOCATION" \
  --project="$KMS_PROJECT_ID" \
  --member="serviceAccount:${SERVICE_AGENT_CORE}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" -q

# 3. Compute System Service Agent (for container disk encryption)
gcloud kms keys add-iam-policy-binding "$KEY_NAME" \
  --keyring="$KEY_RING" \
  --location="$LOCATION" \
  --project="$KMS_PROJECT_ID" \
  --member="serviceAccount:${SERVICE_AGENT_COMPUTE}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" -q || true

FULL_KEY_NAME=$(gcloud kms keys describe "$KEY_NAME" \
  --keyring="$KEY_RING" \
  --location="$LOCATION" \
  --project="$KMS_PROJECT_ID" \
  --format="value(name)")

echo "=========================================="
echo "KMS setup completed successfully!"
echo "Full Key Resource Name: $FULL_KEY_NAME"
echo "=========================================="
