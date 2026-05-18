"""Google Cloud implementations of the storage protocols.

Tenants live as Firestore documents in the ``tenants`` collection.
Artifacts live as objects in a GCS bucket under ``sessions/`` (with
``incomplete/`` and ``complete/`` subprefixes).

Required env vars (set at Cloud Run deploy time):

  * ``GCP_PROJECT``      Project ID for Firestore.
  * ``GCS_BUCKET``       Bucket name for artifact storage.
  * ``FIRESTORE_DATABASE`` (optional) Database id; default ``"(default)"``.

Auth uses Application Default Credentials, so on Cloud Run / GCE / GKE the
attached service account is picked up automatically.

Security model:
  Tenant API keys are stored as plain document fields in Firestore. Lock
  down the Firestore IAM so only this service can read them. Same trust
  model as any BYOK SaaS; for stricter setups, swap in Cloud KMS to encrypt
  the key field before writing.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime
from typing import Any

# Cloud SDK imports are inside the class methods so ``import storage`` works
# in environments where ``google-cloud-*`` isn't installed (local default).


def _new_tenant_id() -> str:
    return secrets.token_urlsafe(8)


def _safe_id(tenant_id: str) -> str:
    keep = "".join(c for c in tenant_id if c.isalnum() or c in "-_")
    if not keep or keep != tenant_id:
        raise ValueError(f"Invalid tenant id: {tenant_id!r}")
    return keep


class FirestoreTenantStore:
    """Tenants live in the ``tenants`` collection. One doc per tenant id."""

    COLLECTION = "tenants"

    def __init__(self) -> None:
        from google.cloud import firestore  # type: ignore

        project = os.environ.get("GCP_PROJECT") or None
        db = os.environ.get("FIRESTORE_DATABASE") or "(default)"
        self._client = firestore.Client(project=project, database=db)

    def _doc(self, tenant_id: str):
        return self._client.collection(self.COLLECTION).document(_safe_id(tenant_id))

    def save_tenant(
        self,
        *,
        form_url: str,
        google_api_key: str,
        gemini_model: str,
        form_title: str = "",
        question_count: int = 0,
        tenant_id: str | None = None,
    ) -> str:
        tid = tenant_id or _new_tenant_id()
        payload = {
            "tenant_id": tid,
            "form_url": form_url.strip(),
            "google_api_key": google_api_key.strip(),
            "gemini_model": (gemini_model or "gemini-2.5-flash").strip(),
            "form_title": form_title,
            "question_count": question_count,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._doc(tid).set(payload)
        return tid

    def load_tenant(self, tenant_id: str) -> dict[str, Any]:
        snap = self._doc(tenant_id).get()
        if not snap.exists:
            raise FileNotFoundError(f"No chatbot config for id {tenant_id!r}.")
        return snap.to_dict() or {}

    def tenant_exists(self, tenant_id: str) -> bool:
        try:
            return self._doc(tenant_id).get().exists
        except ValueError:
            return False


def _safe_relative(path: str) -> str:
    """Object keys can't escape the configured prefix."""
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"Unsafe artifact path: {path!r}")
    return path


class GCSArtifactStore:
    """Artifacts live under ``<prefix>/<logical-path>`` in the configured bucket."""

    def __init__(self) -> None:
        from google.cloud import storage  # type: ignore

        bucket_name = os.environ.get("GCS_BUCKET")
        if not bucket_name:
            raise RuntimeError(
                "GCS_BUCKET env var must be set when STORAGE_BACKEND=cloud."
            )
        self._prefix = (os.environ.get("GCS_PREFIX") or "sessions").strip("/")
        self._client = storage.Client(project=os.environ.get("GCP_PROJECT") or None)
        self._bucket = self._client.bucket(bucket_name)

    def _key(self, path: str) -> str:
        return f"{self._prefix}/{_safe_relative(path)}"

    def _blob(self, path: str):
        return self._bucket.blob(self._key(path))

    def write_bytes(self, path: str, data: bytes) -> None:
        self._blob(path).upload_from_string(data)

    def write_text(self, path: str, text: str) -> None:
        self._blob(path).upload_from_string(text, content_type="text/plain; charset=utf-8")

    def exists(self, path: str) -> bool:
        return self._blob(path).exists()

    def delete(self, path: str) -> None:
        blob = self._blob(path)
        if blob.exists():
            blob.delete()

    def move(self, src_path: str, dst_path: str) -> None:
        src = self._blob(src_path)
        if not src.exists():
            return
        self._bucket.copy_blob(src, self._bucket, new_name=self._key(dst_path))
        src.delete()

    def display_path(self, path: str) -> str:
        return f"gs://{self._bucket.name}/{self._key(path)}"
