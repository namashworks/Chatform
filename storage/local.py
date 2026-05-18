"""Local-filesystem implementations of the storage protocols.

Tenants live in ``tenants/<id>.json``. Artifacts live under ``sessions/``
(``incomplete/`` or ``complete/`` subfolders). This is the default backend
and the only one with no external dependencies — perfect for dev and CI.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from config import SESSIONS_DIR, TENANTS_DIR


def _new_tenant_id() -> str:
    return secrets.token_urlsafe(8)


def _safe_id(tenant_id: str) -> str:
    """Reject anything that could escape the tenants directory."""
    keep = "".join(c for c in tenant_id if c.isalnum() or c in "-_")
    if not keep or keep != tenant_id:
        raise ValueError(f"Invalid tenant id: {tenant_id!r}")
    return keep


class LocalTenantStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or TENANTS_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, tenant_id: str) -> Path:
        return self._root / f"{_safe_id(tenant_id)}.json"

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
        self._path(tid).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return tid

    def load_tenant(self, tenant_id: str) -> dict[str, Any]:
        path = self._path(tenant_id)
        if not path.exists():
            raise FileNotFoundError(f"No chatbot config for id {tenant_id!r}.")
        return json.loads(path.read_text(encoding="utf-8"))

    def tenant_exists(self, tenant_id: str) -> bool:
        try:
            return self._path(tenant_id).exists()
        except ValueError:
            return False


def _safe_relative(path: str) -> Path:
    """Reject paths that try to escape the sessions root."""
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Unsafe artifact path: {path!r}")
    return p


class LocalArtifactStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or SESSIONS_DIR
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, path: str) -> Path:
        return self._root / _safe_relative(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def write_text(self, path: str, text: str) -> None:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def exists(self, path: str) -> bool:
        return self._path(path).exists()

    def delete(self, path: str) -> None:
        p = self._path(path)
        if p.exists():
            p.unlink()

    def move(self, src_path: str, dst_path: str) -> None:
        src = self._path(src_path)
        dst = self._path(dst_path)
        if not src.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        src.replace(dst)

    def display_path(self, path: str) -> str:
        full = self._path(path)
        try:
            return str(full.relative_to(Path.cwd()))
        except ValueError:
            return str(full)
