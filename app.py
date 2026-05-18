"""Streamlit app: a generator page that mints chatbot URLs, plus the chatbot itself.

Routing is by query param:
    /                 -> generator UI (paste form URL + API key, get share link)
    /?c=<tenant_id>   -> chatbot loaded from tenants/<tenant_id>.json
    /?c=env           -> chatbot loaded from environment variables (legacy mode)
"""
from __future__ import annotations

import html as _html
import json
import time
from datetime import datetime
from typing import Any

import streamlit as st

from chatbot import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, Chatbot, TurnResult
from config import Settings, init_ssl, public_base_url
from form_parser import Form, FormAccessError, Question, QuestionType, parse_form
from form_submitter import SubmissionResult, submit
from report_writer import write_conversation_pdf, write_filled_form_pdf
from storage import get_artifact_store
from tenant_store import load_tenant, save_tenant, tenant_exists


# Session lifecycle statuses written into each saved JSON.
STATUS_IN_PROGRESS = "in_progress"               # mid-conversation, answers being collected
STATUS_PENDING_SUBMIT = "answered_pending_submit"  # all questions answered, awaiting user click
STATUS_SUBMITTED = "submitted"                    # Google confirmed the submission
STATUS_SUBMIT_FAILED = "submission_failed"        # submit attempted but Google didn't confirm

# Logical subfolders within the artifact store. The local backend maps these
# to ``sessions/incomplete/`` and ``sessions/complete/`` on disk; the GCS
# backend maps them to identically-named prefixes inside the bucket.
INCOMPLETE_FOLDER = "incomplete"
COMPLETE_FOLDER = "complete"


st.set_page_config(page_title="Chatform", page_icon="💬", layout="centered")


# ---------- session bootstrap ----------

def _init_state(settings: Settings) -> None:
    """Initialize chatbot state once per session for the given settings."""
    if "settings" in st.session_state:
        return

    init_ssl(settings)
    form = parse_form(settings.form_url, verify=settings.ssl_verify)
    chatbot = Chatbot(api_key=settings.google_api_key, model=settings.gemini_model)

    st.session_state.settings = settings
    st.session_state.form = form
    st.session_state.chatbot = chatbot
    st.session_state.conversation = []           # full display history: [{role, content}]
    st.session_state.answers = {}                # {entry_id: canonical value}
    st.session_state.uploaded_files = {}         # {entry_id: {name, size}}
    st.session_state.q_idx = 0
    st.session_state.q_turns = []                # per-question turn history for the LLM
    st.session_state.phase = "intro"             # intro | asking | done | submitting | submitted
    st.session_state.submission: SubmissionResult | None = None
    now = datetime.now()
    st.session_state.session_id = now.strftime("%Y%m%d-%H%M%S")
    st.session_state.started_at = now.isoformat(timespec="seconds")
    st.session_state.session_path = None
    st.session_state.language = DEFAULT_LANGUAGE  # canonical Gemini-side label


def _current_question() -> Question | None:
    form: Form = st.session_state.form
    idx: int = st.session_state.q_idx
    if 0 <= idx < len(form.questions):
        return form.questions[idx]
    return None


# ---------- conversation helpers ----------

def _append(role: str, content: str) -> None:
    st.session_state.conversation.append({"role": role, "content": content})


def _ask_current_question(*, is_first: bool = False) -> None:
    q = _current_question()
    if q is None:
        st.session_state.phase = "done"
        return
    chatbot: Chatbot = st.session_state.chatbot
    language: str = st.session_state.get("language", DEFAULT_LANGUAGE)
    with st.spinner("Preparing the next question…"):
        phrasing = chatbot.phrase_question(q, is_first=is_first, language=language)
    _append("assistant", phrasing)
    st.session_state.q_turns = [{"role": "assistant", "content": phrasing}]


def _advance_question(answer: Any) -> None:
    q = _current_question()
    if q is not None:
        st.session_state.answers[q.entry_id] = answer
    st.session_state.q_idx += 1
    st.session_state.q_turns = []
    if _current_question() is None:
        st.session_state.phase = "done"
        _append("assistant", _wrap_up_text())
        _save_progress(STATUS_PENDING_SUBMIT)
    else:
        _ask_current_question(is_first=False)
        _save_progress(STATUS_IN_PROGRESS)


def _skip_current_question() -> None:
    q = _current_question()
    if q is None:
        return
    _append("user", "_(skipped)_")
    if q.required:
        _append("assistant", "This one's required, so I'll need an answer before we move on.")
        return
    _append("assistant", "No problem — skipping that one.")
    st.session_state.answers[q.entry_id] = None
    st.session_state.q_idx += 1
    st.session_state.q_turns = []
    if _current_question() is None:
        st.session_state.phase = "done"
        _append("assistant", _wrap_up_text())
        _save_progress(STATUS_PENDING_SUBMIT)
    else:
        _ask_current_question(is_first=False)
        _save_progress(STATUS_IN_PROGRESS)


def _wrap_up_text() -> str:
    return (
        "That's the last question — thank you! Review the summary on the right and "
        "hit **Submit form** when you're ready to send it in."
    )


def _handle_user_reply(reply: str) -> None:
    q = _current_question()
    if q is None:
        return
    chatbot: Chatbot = st.session_state.chatbot
    _append("user", reply)
    st.session_state.q_turns.append({"role": "user", "content": reply})

    language: str = st.session_state.get("language", DEFAULT_LANGUAGE)
    with st.spinner("Thinking…"):
        result: TurnResult = chatbot.process_reply(
            q, st.session_state.q_turns, reply, language=language
        )
    _append("assistant", result.message or "Got it.")
    st.session_state.q_turns.append({"role": "assistant", "content": result.message or ""})

    if result.action == "accept":
        _advance_question(result.answer)
    else:
        # Clarification turn — still persist so an abandoned mid-question
        # conversation isn't lost.
        _save_progress(STATUS_IN_PROGRESS)


def _build_payload(status: str) -> dict[str, Any]:
    form: Form = st.session_state.form
    return {
        "session_id": st.session_state.session_id,
        "status": status,
        "form_id": form.form_id,
        "form_title": form.title,
        "form_url": st.session_state.settings.form_url,
        "started_at": st.session_state.started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "current_question_index": st.session_state.q_idx,
        "total_questions": len(form.questions),
        "conversation": st.session_state.conversation,
        "answers": {
            q.title: st.session_state.answers.get(q.entry_id) for q in form.questions
        },
        "raw_answers": st.session_state.answers,
        "uploaded_files": st.session_state.uploaded_files,
        "submission": (
            {
                "ok": st.session_state.submission.ok,
                "status_code": st.session_state.submission.status_code,
                "detail": st.session_state.submission.detail,
            }
            if st.session_state.submission
            else None
        ),
    }


def _session_id() -> str:
    return st.session_state.session_id


def _artifact_paths(folder: str) -> dict[str, str]:
    """Return the standard per-session logical paths inside ``folder``.

    These are backend-agnostic (e.g. ``"incomplete/20260513-150000.json"``);
    the active :class:`ArtifactStore` maps them to local files or GCS objects.
    """
    sid = _session_id()
    return {
        "json": f"{folder}/{sid}.json",
        "conversation_pdf": f"{folder}/{sid}_conversation.pdf",
        "filled_form_pdf": f"{folder}/{sid}_filled_form.pdf",
        "response_html": f"{folder}/{sid}_submission_response.html",
    }


def _write_json(folder: str, status: str) -> str:
    payload = _build_payload(status)
    path = _artifact_paths(folder)["json"]
    get_artifact_store().write_text(
        path, json.dumps(payload, indent=2, default=str)
    )
    return path


def _write_pdfs(folder: str, status: str) -> None:
    """Regenerate the conversation + filled-form PDFs in ``folder``."""
    form: Form = st.session_state.form
    paths = _artifact_paths(folder)
    submission_ok: bool | None
    if st.session_state.submission is None:
        submission_ok = None
    else:
        submission_ok = bool(st.session_state.submission.ok)

    convo_bytes = write_conversation_pdf(
        form,
        st.session_state.conversation,
        session_id=_session_id(),
        started_at=st.session_state.started_at,
        updated_at=datetime.now().isoformat(timespec="seconds"),
        status=status,
    )
    filled_bytes = write_filled_form_pdf(
        form,
        st.session_state.answers,
        session_id=_session_id(),
        updated_at=datetime.now().isoformat(timespec="seconds"),
        status=status,
        submission_ok=submission_ok,
    )

    store = get_artifact_store()
    store.write_bytes(paths["conversation_pdf"], convo_bytes)
    store.write_bytes(paths["filled_form_pdf"], filled_bytes)


def _maybe_write_response_html(folder: str) -> None:
    """If a submission was attempted, dump Google's response HTML for debugging."""
    sub: SubmissionResult | None = st.session_state.submission
    if sub is None or not sub.response_html:
        return
    path = _artifact_paths(folder)["response_html"]
    get_artifact_store().write_text(path, sub.response_html)


_END_STATES = {STATUS_PENDING_SUBMIT, STATUS_SUBMITTED, STATUS_SUBMIT_FAILED}


def _save_progress(status: str) -> str:
    """Persist the session JSON (and end-state PDFs) in the ``incomplete/`` area.

    The JSON is rewritten on every call so one session has exactly one
    canonical record. PDFs are (re)generated only at end-of-conversation
    moments (all questions answered, submit attempted, submit failed) since
    they're more expensive to build than the JSON.

    The session is only moved to ``complete/`` after a confirmed submission
    via :func:`_finalize_to_complete`.
    """
    json_path = _write_json(INCOMPLETE_FOLDER, status)
    if status in _END_STATES:
        _write_pdfs(INCOMPLETE_FOLDER, status)
        _maybe_write_response_html(INCOMPLETE_FOLDER)
    st.session_state.session_path = json_path
    return json_path


def _finalize_to_complete() -> str:
    """Move every session artifact from ``incomplete/`` to ``complete/`` after submit."""
    json_path = _write_json(COMPLETE_FOLDER, STATUS_SUBMITTED)
    _write_pdfs(COMPLETE_FOLDER, STATUS_SUBMITTED)
    _maybe_write_response_html(COMPLETE_FOLDER)

    # Drop the duplicated copies left behind in incomplete/.
    store = get_artifact_store()
    for src in _artifact_paths(INCOMPLETE_FOLDER).values():
        store.delete(src)

    st.session_state.session_path = json_path
    return json_path


# ---------- sidebar UI ----------

def _render_sidebar() -> None:
    form: Form = st.session_state.form
    with st.sidebar:
        st.subheader(form.title)
        if form.description:
            st.caption(form.description)
        st.divider()

        # Language selector — the LLM speaks the user's language but still
        # maps choice answers to the form's canonical option strings.
        labels = list(SUPPORTED_LANGUAGES.keys())
        current_label = next(
            (l for l, code in SUPPORTED_LANGUAGES.items()
             if code == st.session_state.get("language", DEFAULT_LANGUAGE)),
            labels[0],
        )
        selected_label = st.selectbox(
            "Language",
            options=labels,
            index=labels.index(current_label),
            help="Switch any time. The bot replies in your language; your answers still get submitted to the form.",
        )
        st.session_state.language = SUPPORTED_LANGUAGES[selected_label]
        st.divider()

        total = len(form.questions)
        done = min(st.session_state.q_idx, total)
        st.progress(done / total if total else 1.0, text=f"Question {min(done + 1, total)} of {total}")

        st.divider()
        st.markdown("**Answers so far**")
        for i, q in enumerate(form.questions):
            val = st.session_state.answers.get(q.entry_id)
            if i < st.session_state.q_idx and val is not None:
                st.markdown(f"✅ **{q.title}** — {_format_value(val)}")
            elif i < st.session_state.q_idx:
                st.markdown(f"⏭ **{q.title}** — _skipped_")
            elif i == st.session_state.q_idx:
                st.markdown(f"💬 **{q.title}** — _in progress_")
            else:
                st.markdown(f"○ {q.title}")


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


# ---------- main render ----------

def _render_chat() -> None:
    for msg in st.session_state.conversation:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_question_widgets() -> None:
    """Render context-specific widgets (file upload, skip) under the chat."""
    q = _current_question()
    if q is None:
        return

    cols = st.columns([1, 1, 4])

    if q.type == QuestionType.FILE_UPLOAD:
        with cols[2]:
            uploaded = st.file_uploader(
                f"Upload a file for: {q.title}",
                key=f"upload_{q.entry_id}",
                accept_multiple_files=False,
            )
            already_recorded = q.entry_id in st.session_state.uploaded_files
            if uploaded is not None and not already_recorded:
                st.session_state.uploaded_files[q.entry_id] = {
                    "name": uploaded.name,
                    "size": uploaded.size,
                }
                _append("user", f"_(uploaded: {uploaded.name})_")
                _append(
                    "assistant",
                    "Got the file. Heads up: Google requires sign-in to submit files, so "
                    "this field won't be auto-sent in the anonymous flow — but I've noted it.",
                )
                _advance_question(uploaded.name)
                st.rerun()

    if not q.required:
        with cols[0]:
            if st.button("Skip", key=f"skip_{q.entry_id}", use_container_width=True):
                _skip_current_question()
                st.rerun()


def _render_done_panel() -> None:
    form: Form = st.session_state.form
    st.success("All questions answered.")
    with st.expander("Review your answers", expanded=True):
        for q in form.questions:
            val = st.session_state.answers.get(q.entry_id)
            label = _format_value(val) if val is not None else "_skipped_"
            st.markdown(f"- **{q.title}** — {label}")

    has_files = any(q.type == QuestionType.FILE_UPLOAD for q in form.questions)
    if has_files:
        st.warning(
            "This form has file-upload questions. Google requires the respondent to be "
            "signed in to Google for those, so they cannot be submitted via this "
            "anonymous flow yet. Other fields will still be sent."
        )

    if st.button("Submit form", type="primary"):
        settings: Settings = st.session_state.settings
        with st.spinner("Submitting your response…"):
            result = submit(form, st.session_state.answers, verify=settings.ssl_verify)
            st.session_state.submission = result
            st.session_state.phase = "submitted"
            if result.ok:
                _finalize_to_complete()
            else:
                # Keep the file in incomplete/ so the user can retry.
                _save_progress(STATUS_SUBMIT_FAILED)
            time.sleep(0.3)
        st.rerun()


def _render_submitted_panel() -> None:
    result: SubmissionResult = st.session_state.submission
    folder = COMPLETE_FOLDER if result.ok else INCOMPLETE_FOLDER
    paths = _artifact_paths(folder)
    store = get_artifact_store()

    if result.ok:
        st.success(f"Submitted successfully (HTTP {result.status_code}).")
        st.caption("Your response should now appear in the form's Responses tab on docs.google.com.")
    else:
        st.error(f"Submission did not confirm: {result.detail}")
        st.caption(
            "Files kept in `incomplete/` so you can retry. "
            "Open the saved `*_submission_response.html` to see exactly what Google returned."
        )

    st.markdown("**Artifacts saved:**")
    for label, key in [
        ("JSON record", "json"),
        ("Conversation PDF", "conversation_pdf"),
        ("Filled-form PDF", "filled_form_pdf"),
        ("Submission response (HTML)", "response_html"),
    ]:
        p = paths[key]
        if store.exists(p):
            st.markdown(f"- {label}: `{store.display_path(p)}`")

    if st.button("Start over"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ---------- routing ----------

def _query_param(name: str) -> str | None:
    """Read a single query-string value, tolerating Streamlit's list returns."""
    val = st.query_params.get(name)
    if isinstance(val, list):
        val = val[0] if val else None
    return val if val else None


def _resolve_settings() -> Settings | None:
    """Decide which Settings to run the chatbot with based on ``?c=...``.

    Returns ``None`` when no chatbot is requested — caller renders the
    generator instead.
    """
    config_id = _query_param("c") or _query_param("config")
    if not config_id:
        return None

    if config_id == "env":
        return Settings.load()

    if not tenant_exists(config_id):
        st.error("Chatbot link is invalid or expired")
        st.markdown(
            f"No chatbot config was found for id `{_html.escape(config_id)}`. "
            "Ask whoever shared this link to regenerate it on the **Generate** page."
        )
        st.stop()

    tenant = load_tenant(config_id)
    return Settings.from_values(
        google_api_key=tenant["google_api_key"],
        form_url=tenant["form_url"],
        gemini_model=tenant.get("gemini_model"),
    )


# ---------- chatbot mode ----------

def _run_chatbot(settings: Settings) -> None:
    try:
        _init_state(settings)
    except FormAccessError as exc:
        st.error("Form not accessible")
        st.markdown(str(exc))
        st.stop()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not start: {exc}")
        st.stop()

    form: Form = st.session_state.form
    st.title("📝 " + form.title)

    if st.session_state.phase == "intro":
        if not form.questions:
            st.warning("This form has no answerable questions.")
            st.stop()
        _ask_current_question(is_first=True)
        st.session_state.phase = "asking"
        # Seed the incomplete/ file immediately so even a tab-close right after
        # the greeting leaves a record on disk.
        _save_progress(STATUS_IN_PROGRESS)

    _render_sidebar()
    _render_chat()

    if st.session_state.phase == "asking":
        _render_question_widgets()
        if user_input := st.chat_input("Your reply…"):
            _handle_user_reply(user_input)
            st.rerun()

    elif st.session_state.phase == "done":
        _render_done_panel()

    elif st.session_state.phase == "submitted":
        _render_submitted_panel()


# ---------- generator mode ----------

def _validate_inputs(form_url: str, api_key: str, model: str, ssl_verify: bool) -> tuple[Form, str | None]:
    """Return the parsed form on success. Raises a ``ValueError`` on failure
    with a user-readable message."""
    if not form_url or not api_key:
        raise ValueError("Both Form URL and Google API key are required.")
    if "docs.google.com/forms" not in form_url:
        raise ValueError(
            "That URL doesn't look like a Google Form. Expected something like "
            "`https://docs.google.com/forms/d/e/.../viewform`."
        )
    # 1. Form must parse cleanly.
    try:
        form = parse_form(form_url, verify=ssl_verify)
    except FormAccessError as exc:
        raise ValueError(str(exc)) from exc

    # 2. API key must work against the configured model.
    try:
        Chatbot(api_key=api_key, model=model).ping()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Couldn't reach Gemini with that API key + model `{model}`: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    return form, None


def _embed_snippet(url: str) -> str:
    safe = _html.escape(url, quote=True)
    return (
        f'<iframe src="{safe}" '
        'width="100%" height="720" '
        'style="border:0; border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,0.08);" '
        'allow="clipboard-write"></iframe>'
    )


def _run_generator() -> None:
    st.title("💬 Chatform")
    st.caption("Turn any Google Form into a conversation.")
    st.markdown(
        "Turn any **public** Google Form into a conversational chatbot. "
        "Paste the form URL and a Gemini API key, press **Generate**, and you'll "
        "get a shareable chatbot link plus an HTML snippet to embed it on a website."
    )

    with st.form("generator", clear_on_submit=False):
        form_url = st.text_input(
            "Google Form URL",
            placeholder="https://docs.google.com/forms/d/e/.../viewform",
            help="Must be public (anyone with the link, no Google sign-in required).",
        )
        api_key = st.text_input(
            "Google API key (Gemini)",
            type="password",
            help="Get one at https://aistudio.google.com/apikey",
        )
        model = st.selectbox(
            "Gemini model",
            options=["gemini-2.5-flash", "gemini-3.0-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
            index=0,
            help=(
                "Flash models are fast and cheap and what we recommend for the chatbot. "
                "Pro is more capable but noticeably slower per turn."
            ),
        )
        submitted = st.form_submit_button("Generate chatbot", type="primary")

    if not submitted:
        _render_generator_help()
        return

    init_ssl()  # reads USE_SYSTEM_CERTS / SSL_VERIFY straight from env
    ssl_verify = _as_env_bool("SSL_VERIFY", True)

    with st.spinner("Validating form URL and API key…"):
        try:
            form, _ = _validate_inputs(form_url, api_key, model, ssl_verify)
        except ValueError as exc:
            st.error(str(exc))
            return

    tenant_id = save_tenant(
        form_url=form_url,
        google_api_key=api_key,
        gemini_model=model,
        form_title=form.title,
        question_count=len(form.questions),
    )

    share_url = f"{public_base_url()}/?c={tenant_id}"
    st.success(f"Chatbot ready for **{form.title}** ({len(form.questions)} questions).")
    st.divider()

    st.subheader("Shareable chatbot URL")
    st.code(share_url, language=None)
    st.link_button("Open the chatbot", share_url, type="primary")

    st.subheader("HTML embed for your website")
    embed = _embed_snippet(share_url)
    st.code(embed, language="html")
    with st.expander("Live preview"):
        st.components.v1.html(embed, height=760, scrolling=False)

    st.caption(
        f"Saved as tenant `{tenant_id}`. Generate another by refreshing this page."
    )


def _render_generator_help() -> None:
    with st.expander("Before you start — make sure your form is public"):
        st.markdown(
            "1. In the Google Forms editor, open **Settings ⚙ → Responses**.\n"
            "2. Turn **off** *Collect email addresses*, *Limit to 1 response*, "
            "and *Restrict to users in your domain* (if shown).\n"
            "3. **Send → Link** — open the link in a private/incognito window. "
            "If it loads the form directly (no Google sign-in), it's public."
        )
    with st.expander("How sharing works"):
        st.markdown(
            f"- The chatbot is hosted at **{public_base_url()}**. Set the "
            "`PUBLIC_BASE_URL` env var if you deploy behind a different hostname.\n"
            "- Each generated link contains a short tenant ID (e.g. `?c=abc123`). "
            "Anyone with the link can fill the form via chat — no account needed.\n"
            "- The HTML embed snippet drops the chatbot into your site as an iframe."
        )


def _as_env_bool(name: str, default: bool) -> bool:
    import os
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


# ---------- entrypoint ----------

def main() -> None:
    settings = _resolve_settings()
    if settings is None:
        _run_generator()
    else:
        _run_chatbot(settings)


if __name__ == "__main__":
    main()
