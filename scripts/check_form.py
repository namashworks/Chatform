"""Smoke-test the form URL: fetch + parse + print question summary.

Run from project root:
    .venv/Scripts/python.exe scripts/check_form.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, init_ssl  # noqa: E402
from form_parser import parse_form  # noqa: E402


def main() -> int:
    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"[FAIL] Could not load settings: {exc}")
        return 1
    init_ssl(settings)
    print(f"[ OK ] TLS: use_system_certs={settings.use_system_certs}, ssl_verify={settings.ssl_verify}")
    print(f"[ OK ] FORM_URL: {settings.form_url}")

    try:
        form = parse_form(settings.form_url, verify=settings.ssl_verify)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] parse_form raised: {type(exc).__name__}: {exc}")
        return 1

    print(f"[ OK ] Form ID  : {form.form_id}")
    print(f"[ OK ] Title    : {form.title}")
    print(f"[ OK ] Submit to: {form.submit_url}")
    print(f"[ OK ] Questions: {len(form.questions)}")
    print()
    for i, q in enumerate(form.questions, 1):
        req = "required" if q.required else "optional"
        extra = ""
        if q.options:
            extra = f" — options: {q.options}"
        elif q.scale_min is not None:
            extra = f" — scale {q.scale_min}–{q.scale_max} ({q.scale_labels[0]!r}…{q.scale_labels[1]!r})"
        print(f"  {i:>2}. [{q.type.name}, {req}] {q.title}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
