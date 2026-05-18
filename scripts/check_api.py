"""Smoke-test the Gemini API key and model wired up in .env.

Run from project root:
    .venv/Scripts/python.exe scripts/check_api.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root is on sys.path so we can import config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, init_ssl  # noqa: E402


def main() -> int:
    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"[FAIL] Could not load settings: {exc}")
        return 1
    init_ssl(settings)
    print(f"[ OK ] TLS: use_system_certs={settings.use_system_certs}, ssl_verify={settings.ssl_verify}")

    masked = settings.google_api_key[:4] + "…" + settings.google_api_key[-4:]
    print(f"[ OK ] GOOGLE_API_KEY loaded ({len(settings.google_api_key)} chars, {masked})")
    print(f"[ OK ] Model: {settings.gemini_model}")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        print(f"[FAIL] google-genai not installed: {exc}")
        return 1

    client = genai.Client(api_key=settings.google_api_key)
    print(f"[ OK ] google-genai client created")

    prompt = "Reply with exactly: pong"
    print(f"\nSending test prompt: {prompt!r}")
    started = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=20),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] API call raised: {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.perf_counter() - started

    text = (resp.text or "").strip()
    print(f"[ OK ] Response in {elapsed:.2f}s: {text!r}")

    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        print(
            f"[ OK ] Tokens — prompt: {getattr(usage, 'prompt_token_count', '?')}, "
            f"response: {getattr(usage, 'candidates_token_count', '?')}, "
            f"total: {getattr(usage, 'total_token_count', '?')}"
        )

    # Also exercise JSON mode (what chatbot.process_reply relies on)
    print("\nTesting JSON mode (used by chatbot.process_reply)…")
    try:
        json_resp = client.models.generate_content(
            model=settings.gemini_model,
            contents='Return JSON: {"action": "accept", "answer": 42}',
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                max_output_tokens=60,
            ),
        )
        print(f"[ OK ] JSON-mode response: {(json_resp.text or '').strip()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] JSON-mode call raised: {type(exc).__name__}: {exc}")
        return 1

    print("\nAll checks passed — the Gemini key + model are working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
