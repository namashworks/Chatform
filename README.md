# Google Forms → Conversational Chatbot

Turn any public Google Form into a one-question-at-a-time chat experience.
A Gemini-powered assistant asks each question conversationally, clarifies
whenever the user is confused, validates the answer against the form's
constraints, and submits the response through Google's `/formResponse`
endpoint.

## How it works

1. **Form parsing** — the app fetches the form's `viewform` HTML and extracts
   the embedded `FB_PUBLIC_LOAD_DATA_` JSON to discover every question, its
   type, options, required flag, and `entry.XXXX` submission ID. No Google
   OAuth is needed for public forms.
2. **Conversation** — for each question, Gemini opens with a friendly
   phrasing. Every user reply is sent back to Gemini with the question's
   constraints; the model either **accepts** the answer (mapping free-form
   text to a canonical form value) or **clarifies** in simpler language.
3. **Submission** — accepted answers are formatted per question type and
   POSTed to `https://docs.google.com/forms/d/e/{FORM_ID}/formResponse`.
   The full conversation and final answers are also saved to
   `sessions/<timestamp>.json` for audit.

## Two ways to use it

The app has a single Streamlit entry point with two modes, routed by query string:

| URL | Mode | What happens |
| --- | --- | --- |
| `/` | **Generator** | Paste a form URL + Gemini API key, press *Generate*, get a shareable chatbot link and an HTML embed snippet. |
| `/?c=<tenant_id>` | **Chatbot** | Runs the conversational form-filler using the tenant config saved by the generator. This is the URL you share. |
| `/?c=env` | Chatbot (legacy) | Runs the chatbot from `FORM_URL` / `GOOGLE_API_KEY` env vars — handy for testing. |

Each tenant config (form URL + API key + model) is stored as `tenants/<id>.json`.
The folder is `.gitignored`; treat it as you would `.env` since it contains
plaintext API keys.

## Setup

```powershell
# 1. Install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. (Optional) Configure env. Only needed for legacy /?c=env mode and for
#    PUBLIC_BASE_URL — the generator UI collects per-tenant values in the browser.
copy .env.example .env
# Edit .env and at minimum set:
#   PUBLIC_BASE_URL — where this app is reachable (defaults to http://localhost:8501)
#   GOOGLE_API_KEY  — only if you want /?c=env to work
#   FORM_URL        — only if you want /?c=env to work

# 3. Run
streamlit run app.py
```

Open `http://localhost:8501`, paste your form URL and Gemini API key, press
**Generate chatbot**. You'll get back:

- A **shareable URL** — send to respondents instead of (or alongside) the
  Google Forms link.
- An **HTML iframe snippet** — drop into any web page to embed the chatbot.
- A **live preview** — try the chatbot inline before sharing.

## Running it so it survives the terminal closing

Streamlit *is* the web server — when you press Ctrl+C or close the terminal
that ran `streamlit run`, the chatbot dies with it. For local persistent
runs on Windows, use the included scripts:

```powershell
# Start in a separate minimized window (closing your terminal doesn't kill it)
scripts\start_server.bat              # defaults to port 8501
scripts\start_server.bat 9000         # custom port

# Stop (finds whatever is listening on that port and kills it)
scripts\stop_server.bat
scripts\stop_server.bat 9000
```

Logs go to `logs\streamlit.log`. The detached window is labeled
`Form Chatbot Server (port 8501)` — you can also stop the server by closing
that window.

For real "always-on" deployment, prefer one of:

| Option | When to use it |
| --- | --- |
| **Streamlit Community Cloud** | Easiest. Push to GitHub, connect, deploy. Free tier covers most teams. |
| **NSSM** (Non-Sucking Service Manager) | You want it as a Windows service — `nssm install FormChatbot ".venv\Scripts\python.exe" "-m streamlit run app.py --server.headless true"`. |
| **Task Scheduler** | Built into Windows. Trigger "At log on" / "At startup", action runs `start_server.bat`. |
| **Docker + reverse proxy** | Multi-host production. Streamlit behind nginx / Caddy. |

Whichever you pick, set `PUBLIC_BASE_URL` to the externally-reachable host
so the generator's shareable links and embed snippets point at the right
place.

## Production deployment — Google Cloud Run

The recommended production path. The app ships with everything you need:
a multi-stage `Dockerfile`, a `cloudbuild.yaml` pipeline, a one-shot
bootstrap script (`scripts/deploy_cloud_run.sh`), and a pluggable storage
backend that uses **Firestore** for tenants and **Cloud Storage** for
session artifacts so it scales across instances and survives restarts.

### What you get

| Concern | How it's handled |
| --- | --- |
| Always-on URL | Cloud Run service with `--allow-unauthenticated` (or lock down with IAM) |
| Autoscaling | `--min-instances=0 --max-instances=5` (configurable) |
| HTTPS + custom domain | Cloud Run provides HTTPS; map a custom domain in the console |
| Sticky chat sessions | `--session-affinity` pins a respondent to one instance |
| Tenant configs | Firestore `tenants` collection (one doc per tenant id) |
| Session JSON + PDFs | GCS bucket under `sessions/incomplete/` and `sessions/complete/` prefixes |
| Secrets | `GOOGLE_API_KEY` from Secret Manager, mounted as an env var |
| Container best practices | Multi-stage build, non-root user, `.dockerignore` excludes secrets and local state |

### One-shot deploy

```bash
# From a Cloud Shell or any machine with gcloud + auth configured
export PROJECT_ID=your-gcp-project
export REGION=us-central1
export BUCKET=your-gcp-project-form-chatbot-sessions   # must be globally unique
./scripts/deploy_cloud_run.sh
```

The script is idempotent — it creates the Artifact Registry repo, GCS
bucket, Firestore database, and Secret Manager secret on first run, then
delegates to `cloudbuild.yaml` for the build + push + deploy. Re-running
just redeploys with the latest code.

After the first deploy, set the public hostname so generated share URLs
point at the right place:

```bash
URL=$(gcloud run services describe form-chatbot --region=$REGION --format='value(status.url)')
gcloud run services update form-chatbot --region=$REGION --update-env-vars=PUBLIC_BASE_URL=$URL
```

### Streamlit Community Cloud (alternative)

If you don't need GCP integration, push to GitHub and connect at
`share.streamlit.io`. Set `GOOGLE_API_KEY` and `PUBLIC_BASE_URL` as app
secrets in the Streamlit UI. Leave `STORAGE_BACKEND=local` (the default).
Note that Streamlit Cloud's filesystem is ephemeral — tenants/sessions
wipe on redeploy. Fine for demos, not for real users.

## Storage backends

The app is backend-agnostic via the `storage` package. Set
`STORAGE_BACKEND` to choose:

| Backend | When | Files |
| --- | --- | --- |
| `local` (default) | Dev, single-machine deploys | `storage/local.py` — uses `tenants/` and `sessions/` dirs on disk |
| `cloud` | Cloud Run / GKE / multi-instance | `storage/cloud.py` — Firestore + GCS via Application Default Credentials |

Both implement the same two protocols (`TenantStore`, `ArtifactStore`) from
`storage/protocol.py`, so swapping is a one-env-var change. Adding a third
backend (DynamoDB + S3, MinIO, Postgres, etc.) means writing one new file
that implements those protocols.

## Multi-language conversation

A language picker lives in the chatbot sidebar. The bot speaks the
selected language for the entire conversation; for text answers it
preserves the user's wording so the form receives their actual reply, but
for choice questions it maps the user's translated reply back to the
form's canonical option string. Supported out of the box:

English, Spanish, French, German, Portuguese, Italian, Simplified Chinese,
Japanese, Korean, Arabic, Hindi, Russian. Add more in
`chatbot.SUPPORTED_LANGUAGES`.

## Supported question types

| Type | Supported | Notes |
| --- | --- | --- |
| Short answer | ✅ | |
| Paragraph | ✅ | |
| Multiple choice | ✅ | "Other" option recognized |
| Dropdown | ✅ | |
| Checkboxes | ✅ | Multi-select |
| Linear scale | ✅ | Validates the integer range |
| Date | ✅ | ISO `YYYY-MM-DD` |
| Time | ✅ | 24-hour `HH:MM` |
| File upload | ⚠️ | UI accepts the file, but Google requires the respondent to be signed in to submit it. Anonymous submission skips this field. |
| Grid / multi-row | ❌ | Planned |
| Section branching | ❌ | Planned |

## Troubleshooting

### `SSLError: self-signed certificate in certificate chain`

Symptom — `Could not start: HTTPSConnectionPool(host='docs.google.com', ...) [SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain`.

This means something between you and Google is re-signing TLS traffic with its
own root CA — typically a corporate VPN/proxy (Zscaler, Netskope, Cisco
Umbrella) or an antivirus with HTTPS inspection (Kaspersky, ESET, BitDefender).
Google never serves a self-signed cert.

The app already opts into `truststore.inject_into_ssl()` on startup
(`USE_SYSTEM_CERTS=true` in `.env`), so if your IT department installed the
corporate root CA in Windows' certificate store it will be trusted
automatically. If you still hit the error:

1. **Confirm the corporate CA is installed in Windows.** Run
   `certmgr.msc` → *Trusted Root Certification Authorities* and look for your
   company's / proxy vendor's CA. If it isn't there, ask IT to push it.
2. **Re-run the smoke tests** to confirm the route:
   ```powershell
   .venv\Scripts\python.exe scripts\check_api.py    # Gemini reachability
   .venv\Scripts\python.exe scripts\check_form.py   # Google Forms reachability
   ```
3. **Emergency unblock (insecure):** set `SSL_VERIFY=false` in `.env`. This
   disables verification for the form fetch and submission only. Use this
   only on a trusted machine while debugging, then turn it back on.

### `Submission did not confirm: Submission not confirmed (HTTP 200)`

Google returned 200 but our success markers weren't present. Two common
causes:

1. **Missing `fbzx` token.** Some forms reject submissions that don't include
   the form-session token; Google returns 200 but doesn't record the
   response. The parser now extracts `fbzx` from the form HTML and the
   submitter includes it in every POST — if this error reappears after the
   fix, the form may have changed.
2. **A required field is empty or invalid.** Google re-renders the form with
   the user's answers and an error banner. The submitter now flags this
   case explicitly (`"Google re-rendered the form ..."`).

The exact response Google sent is dumped to
`sessions/<folder>/<id>_submission_response.html` whenever a submission is
attempted — open that file to see what was actually returned.

### `Could not find FB_PUBLIC_LOAD_DATA_ in the form HTML`

The form is probably not public, requires Google sign-in, or the URL isn't the
`viewform` link. Open the form in an incognito window — if you're asked to
sign in, change the form's sharing settings to "Anyone with the link" and turn
off "Collect email" / "Limit to one response".

## Known limitations

- **File uploads** require OAuth (Google enforces a sign-in for any form
  containing a file question). This is a planned follow-up.
- **Section branching** ("Go to section based on answer") isn't yet honored —
  the chatbot walks questions in declared order.
- **Grids** (multiple-choice grid / checkbox grid) are skipped for now.
- The form must be set to accept responses from "anyone with the link" and
  must not require Google sign-in.

## File layout

```
app.py                          Streamlit entry point — generator + chatbot router
chatbot.py                      Gemini conversation manager (phrase, process_reply, languages)
form_parser.py                  Parses FB_PUBLIC_LOAD_DATA_ + fbzx into Question/Form
form_submitter.py               Builds + POSTs the form-encoded response with fbzx
config.py                       Env-driven settings, TLS init, public_base_url helper
report_writer.py                Conversation + filled-form PDF generators (return bytes)
tenant_store.py                 Backwards-compat facade over storage.get_tenant_store()
storage/
├── __init__.py                 Factory: picks backend from STORAGE_BACKEND env
├── protocol.py                 TenantStore + ArtifactStore Protocols
├── local.py                    Local-filesystem backend (default for dev)
└── cloud.py                    Firestore + GCS backend (production)
Dockerfile                      Multi-stage prod image, non-root, Cloud Run-ready
.dockerignore                   Keeps secrets and local state out of the image
cloudbuild.yaml                 Build + push + deploy pipeline for Cloud Build
requirements.txt                Core deps (everything works locally with these)
requirements-cloud.txt          Firestore + GCS client libs for STORAGE_BACKEND=cloud
scripts/
├── deploy_cloud_run.sh         One-shot bootstrap + deploy to Cloud Run
├── start_server.bat            Local detached launcher (Windows)
├── stop_server.bat             Stop a detached local server
├── check_api.py                Smoke-test the Gemini key
└── check_form.py               Smoke-test the form URL + parser
tenants/                        Local-backend only. One JSON per registered chatbot.
                                Contains plaintext API keys — gitignored.
sessions/                       Local-backend only. Cloud backend writes the same
├── incomplete/                 logical layout into a GCS bucket prefix instead.
│                               Auto-saved every turn while the conversation is live.
│                               Any session that didn't end in a confirmed submission
│                               stays here — abandoned, in-progress, or submit-failed.
└── complete/                   Sessions whose final submission was confirmed by
                                Google. Files are *moved* here from incomplete/ on
                                success, so each session lives in exactly one place.
```

### Per-session artifacts

Every session produces up to four files, all named with the same
`<session_id>` prefix and kept together in whichever folder owns the session:

| File | When written | Purpose |
| --- | --- | --- |
| `<id>.json` | Every turn | Machine-readable conversation + answers + lifecycle status |
| `<id>_conversation.pdf` | At end-of-conversation events | Human-readable chat transcript |
| `<id>_filled_form.pdf` | At end-of-conversation events | Form-style readout of question + answer |
| `<id>_submission_response.html` | When submit is attempted | Raw HTML Google returned — debug aid if a submission didn't confirm |

PDFs regenerate at three moments: all questions answered, submission succeeded, and submission failed. The JSON rewrites on every turn so an abandoned mid-question conversation still has a faithful snapshot.

### Session JSON shape

Every saved file contains:

```json
{
  "session_id": "20260512-141530",
  "status": "in_progress | answered_pending_submit | submitted | submission_failed",
  "form_id": "...",
  "form_title": "...",
  "form_url": "...",
  "started_at": "2026-05-12T14:15:30",
  "updated_at": "2026-05-12T14:18:02",
  "current_question_index": 4,
  "total_questions": 18,
  "conversation": [{"role": "...", "content": "..."}, ...],
  "answers": {"Name": "...", "Email Address": "...", ...},
  "raw_answers": {"entry.123456": "...", ...},
  "uploaded_files": {"entry.789": {"name": "cv.pdf", "size": 12345}},
  "submission": {"ok": true, "status_code": 200, "detail": "Submitted."}
}
```

The file is **overwritten in place** after every turn — one session = one file.
The lifecycle is:

| Event | Folder | `status` |
| --- | --- | --- |
| Conversation starts (greeting shown) | `incomplete/` | `in_progress` |
| User answers / skips / uploads a file | `incomplete/` | `in_progress` |
| User asks a clarifying question | `incomplete/` | `in_progress` |
| All questions answered, awaiting click | `incomplete/` | `answered_pending_submit` |
| Submit succeeds (Google confirms) | `complete/` (moved) | `submitted` |
| Submit fails | `incomplete/` (kept) | `submission_failed` |
# Chatform
