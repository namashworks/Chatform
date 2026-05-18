#!/usr/bin/env bash
# ============================================================================
# deploy_cloud_run.sh  -  One-shot manual deploy to Cloud Run.
#
# Bootstraps Artifact Registry + Cloud Storage + Secret Manager + Cloud Run.
# Idempotent: re-running it updates the existing resources rather than
# duplicating them. Safe to run from CI or from a developer laptop.
#
# Prereqs:
#   * gcloud SDK installed and authenticated  (gcloud auth login)
#   * Docker installed (or rely on Cloud Build for the image build)
#   * Billing enabled on the target project
#
# Usage:
#   PROJECT_ID=my-proj REGION=us-central1 BUCKET=my-form-chatbot-bucket \
#     scripts/deploy_cloud_run.sh
# ============================================================================
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID, e.g. PROJECT_ID=my-gcp-project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-form-chatbot}"
REPO="${REPO:-form-chatbot}"
BUCKET="${BUCKET:?Set BUCKET, e.g. BUCKET=my-form-chatbot-bucket}"

echo "==> Project   : $PROJECT_ID"
echo "==> Region    : $REGION"
echo "==> Service   : $SERVICE"
echo "==> Repo      : $REPO"
echo "==> Bucket    : $BUCKET"
echo

gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling required APIs (idempotent)..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  >/dev/null

echo "==> Ensuring Artifact Registry repo '$REPO' exists..."
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION"

echo "==> Ensuring GCS bucket 'gs://$BUCKET' exists..."
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access

echo "==> Ensuring Firestore database (Native mode) exists..."
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 || \
  gcloud firestore databases create --location="$REGION" --type=firestore-native

echo "==> No deployer-side Gemini key needed — each form creator brings their own."
echo

echo "==> Submitting Cloud Build (build + push + deploy)..."
gcloud builds submit \
  --config=cloudbuild.yaml \
  --substitutions=_REGION="$REGION",_REPO="$REPO",_SERVICE="$SERVICE",_BUCKET="$BUCKET"

URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
echo
echo "==> Deployed. Cloud Run URL: $URL"
echo "==> Set PUBLIC_BASE_URL on the service so share links use the real host:"
echo "    gcloud run services update $SERVICE --region=$REGION --update-env-vars=PUBLIC_BASE_URL=$URL"
