# Backend Notes

This backend is a project-oriented wrapper around the existing parser, translator
prompting, glossary, preview, and writer flow. The default storage layer is now
MySQL for document/job/usage state, while original uploads and generated exports
remain on local disk under `backend_storage/`. Local filesystem JSON is still
available by setting `BACKEND_STORE=json`.

## Main Files

- `src/translator/api/app.py`
  FastAPI entrypoint. Defines HTTP routes and converts API errors into HTTP
  responses. It also serves the customer frontend from `/` and `/app`.

- `apps/frontend/`
  Customer-facing web frontend generated from the Stitch MVP direction and wired
  to the FastAPI API. `index.html` is the shell, `styles.css` holds the visual
  system, and `app.js` handles upload, dashboard, preview, translation actions,
  polling, export, and usage views.

- `src/translator/storage/local.py`
  Local JSON document store and section planner. Owns upload artifacts, parsed
  document JSON, section plans, block translations, section translation records,
  glossary JSON, job records, usage records, preview payloads, and partial
  export.

- `src/translator/storage/mysql.py`
  Default MySQL-backed document store. It stores mutable state in MySQL tables
  and keeps original/export files in the same local `backend_storage/` tree.

- `src/translator/services/translation_jobs.py`
  Orchestrates `translate-next`, `retranslate-last`, and `translate-rest`: finds
  the allowed content, builds prompts, calls a translation client, parses model
  output, stores block translations, and updates the glossary.

- `src/translator/billing/credits.py`
  Defines public model tiers, credit packages, signup credits, and the token-to-
  credit conversion used for quotes and job charges. Model names stay server-
  side; the frontend only sees tier labels and credit prices.

- `src/translator/billing/razorpay.py`
  Razorpay adapter. Creates Razorpay Orders from server-side payment orders and
  verifies Razorpay webhook signatures using the raw request body.

- `src/translator/translation/core.py`
  Shared pure translation helpers used by both the CLI and backend: static
  briefs, translation/revision/glossary/review prompts, document-block prompt
  payloads, model-output parsing, table/inline safety normalization, and block
  translation collectors.

- `src/translator/cli/book.py`
  CLI workflow. It now imports shared prompt/parsing helpers from
  `src/translator/translation/core.py`, while keeping CLI-specific model setup, retries, review,
  revision, and artifact writing.

- `src/translator/documents/adapters.py`
  Parses `.txt`, `.docx`, and `.epub` into `ParsedDocument` / `DocumentBlock`.

- `src/translator/documents/writers.py`
  Writes partial or complete translations back to TXT/DOCX/EPUB using
  `translations_by_block_id`.

## Current API Flow

1. `POST /documents/upload`
   Saves the original file, parses it with `load_document`, creates page-sized
   ordered sections, stores the user-selected `source_language`, and writes
   initial empty state files.

2. `GET /documents`
   Returns the current user's document summaries for the dashboard.

3. `GET /documents/{document_id}/sections`
   Returns ordered sections, word counts, translated block counts, and cursor
   status. It also returns rough token/cost estimates using the constant
   `TRANSLATOR_TOKEN_PRICE_PER_1M_USD`. Only the next untranslated section and
   the last translated section are translatable; future sections are locked and
   older translated sections are read-only. The response also includes:

   - `next_section_estimate` for the next 600-word review section.
   - `remaining_estimate` for finishing the rest with 1500-word bulk chunks.

4. `GET /documents/{document_id}/glossary`
   Returns the current document-level glossary. This is useful for debugging and
   later user-editable terminology.

5. `POST /documents/{document_id}/translate-next`
   Creates a background `translate_next` job and returns a `job_id` immediately.
   The frontend should poll `GET /jobs/{job_id}` until the job reaches
   `succeeded` or `failed`. Send an `idempotency_key` so duplicate clicks
   replay the same job instead of translating another section. Translation
   requests carry `source_language` and `model_tier`; if source is omitted, the
   backend falls back to the document's stored source language. The backend
   computes and reserves wallet credits before the model call.

6. `POST /documents/{document_id}/retranslate-last`
   Creates a background `retranslate_last` job for the last fully translated
   section only. Earlier translated sections are intentionally not retranslated
   in this MVP because changing them could make later section context stale.
   This endpoint also supports `idempotency_key`.

7. `GET /jobs/{job_id}`
   Returns job status, progress, message, result, or error.

8. `GET /usage/me`
   Returns the current user's usage summary and successful translation job
   usage records. It also includes daily upload quota and active translation job
   counts for the Usage page.

9. `GET /documents/{document_id}/usage`
   Returns usage records for one owned document.

10. `POST /documents/{document_id}/translate-rest`
   Creates a background `translate_rest` job for all remaining untranslated
   blocks. This endpoint is meant for the point where the user is satisfied with
   section quality and wants to finish the document with fewer, larger model
   calls. Send an `idempotency_key` here too, especially because a duplicate
   request may arrive after the first job has already completed.

11. `GET /documents/{document_id}/preview`
   Returns block-level preview data. Translated blocks show target text;
   untranslated blocks show source text with status `source`.

12. `POST /documents/{document_id}/export`
   Uses the original uploaded document plus the saved block translation map to
   generate a partial or complete translated document.

13. `GET /documents/{document_id}/exports/latest/download`
   Downloads the latest generated export.

14. `GET /billing/model-tiers`
    Returns the three public model tiers: `quick_draft`, `balanced`, and
    `precision`. The response intentionally does not expose provider model names.

15. `GET /billing/wallet`
    Returns the current user's wallet balance and recent credit ledger entries.
    This also creates the one-time signup credit grant if it has not been
    created yet.

16. `GET /billing/credit-packages`
    Returns available credit packages for checkout.

17. `POST /billing/checkout-session`
    Creates a pending payment order. This does not add credits. Credits are added
    only after a verified payment completion/webhook. When Razorpay is
    configured, this returns a Razorpay Order ID and checkout key for the
    frontend Checkout modal. The mock checkout path is disabled unless
    `TRANSLATOR_ENABLE_MOCK_PAYMENTS=1`.

18. `POST /billing/mock-payments/{order_id}/complete`
    Local-development-only payment completion. It requires
    `TRANSLATOR_ENABLE_MOCK_PAYMENTS=1` and `X-Mock-Payment-Secret` matching
    `TRANSLATOR_MOCK_PAYMENT_SECRET`. Do not enable this in production.

19. `POST /billing/razorpay/webhook`
    Public Razorpay webhook endpoint. It verifies the raw request body with
    `RAZORPAY_WEBHOOK_SECRET`, accepts captured payment events, validates the
    Razorpay amount/currency/order/package/owner against the server-side order,
    and then completes the order through the same idempotent wallet flow as mock
    payments.

20. `GET /documents/{document_id}/quote?model_tier=balanced`
    Returns credit estimates for translate-next, retranslate-last, and translate-
    remaining-document using the selected model tier.

## Storage Layout

Default backend:

```text
BACKEND_STORE=mysql
```

MySQL stores:

- `documents`: owner, metadata, parsed document JSON, section plan,
  translations, section records, glossary, and latest export metadata.
- `jobs`: background translate/export job status, payload, result, and errors.
- `usage_records`: successful translation usage ledger for quotas and billing
  previews.
- `payment_orders`: pending/paid checkout orders. A pending order never changes
  wallet balance by itself.
- `credit_ledger`: immutable wallet entries for signup grants, purchases,
  reservations, charges, and refunds.

Local disk still stores heavy files:

```text
backend_storage/
  documents/
    doc_abcd1234ef56/
      original/
        original.docx
      exports/
        translated_document.docx
```

For tests or simple local hacking, set `BACKEND_STORE=json`. That uses the older
filesystem state files:

```text
backend_storage/jobs.json
backend_storage/usage.json
backend_storage/documents/{document_id}/*.json
```

## Auth And Ownership

Authentication has two modes:

- `TRANSLATOR_AUTH_MODE=dev` is the default local mode. It uses
  `X-Dev-User-Id` and `X-Dev-User-Email` headers when provided, otherwise it
  falls back to a local development user.
- `TRANSLATOR_AUTH_MODE=google` is production mode. The frontend must send:

```http
Authorization: Bearer <google_id_token>
```

The backend verifies the token against `GOOGLE_OAUTH_CLIENT_ID`, requires a
verified email, and uses the Google `sub` claim as `owner_user_id`. Email is
stored as `owner_email` for display/support only.

All document, preview, glossary, translation, export, download, and job routes
check ownership. Unknown or unauthorized documents/jobs return `404` so IDs do
not leak across users.

## Usage Accounting

Every successful translation job returns a `usage` object in its job result and
writes one idempotent ledger record to `usage_records`.

Tracked fields include:

- `owner_user_id`, `owner_email`, `owner_auth_provider`
- `document_id`, `job_id`, `job_type`
- `source_language`, `target_language`, `document_type`, `content_form`
- `word_count`, `chunk_count`, `translated_block_count`
- `estimated_input_tokens`, `estimated_output_tokens`
- `estimated_prompt_overhead_tokens`, `estimated_total_tokens`
- `estimated_cost_usd`

Current read endpoints:

```text
GET /usage/me
GET /documents/{document_id}/usage
```

This is estimated usage, not provider-billed exact usage. It is enough for MVP
quotas and user-facing pre-billing estimates. Later, if providers return exact
usage metadata, store both estimated and actual fields.

## Billing And Credits

The customer-facing product should show credits, not raw provider/token costs.
The backend still estimates tokens, then converts them into credits:

```text
estimated_credits = ceil((estimated_total_tokens / 1000)
  * TRANSLATOR_CREDITS_PER_1K_TOKENS
  * model_tier.credit_multiplier)
```

Defaults:

```text
TRANSLATOR_CREDITS_PER_1K_TOKENS=10
TRANSLATOR_SIGNUP_CREDITS=200
TRANSLATOR_QUICK_DRAFT_CREDIT_MULTIPLIER=0.75
TRANSLATOR_BALANCED_CREDIT_MULTIPLIER=1.0
TRANSLATOR_PRECISION_CREDIT_MULTIPLIER=1.6
```

Tier model names are server-side environment values:

```text
TRANSLATOR_QUICK_DRAFT_MODEL=
TRANSLATOR_BALANCED_MODEL=
TRANSLATOR_PRECISION_MODEL=
```

If unset, each tier currently falls back to `GEMINI_MODEL`. This lets us launch
the wallet/tier UX safely before choosing final provider models.

Credit safety rules:

- The frontend never sends balance, granted credits, or charge amounts.
- Checkout creation never grants credits.
- Credits are added only from verified payment completion/webhook logic.
- Razorpay Orders are created server-side from the server package table.
- Razorpay webhooks must be verified with the raw request body and
  `RAZORPAY_WEBHOOK_SECRET`.
- Razorpay payment amount, currency, order ID, package, and owner are
  validated against the pending server order before credits are granted.
- Translation starts only after the backend reserves estimated credits.
- Successful jobs capture the reservation and refund unused reserved credits.
- Failed/skipped jobs release the reservation back to the wallet.
- Replaying the same paid order is idempotent and does not duplicate credits.
- Mock checkout/payment completion must stay disabled in production.
- The JSON store has an in-process billing lock for local safety. Production
  should use MySQL/Postgres plus provider webhooks; the MySQL store uses
  server-side locks and unique ledger indexes for the wallet-critical paths.

Security probes covered:

- Client-supplied `credits`, `amount_cents`, `estimated_credits`, and fake wallet
  balances are ignored.
- A pending checkout order does not change wallet balance.
- Mock payment completion requires both the env flag and server-side secret.
- A user cannot complete another user's payment order.
- Razorpay webhook requests with invalid signatures are rejected.
- Razorpay webhook requests with mismatched amount/currency/order/package/owner
  are rejected.
- Replayed Razorpay captured payment webhooks do not add duplicate credits.
- Duplicate payment completion does not add duplicate credits.
- A user cannot quote or translate another user's document.
- Unknown model tiers are rejected server-side.
- Failed translation jobs refund reserved credits.

Razorpay configuration:

```text
RAZORPAY_KEY_ID=rzp_test_or_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
TRANSLATOR_PUBLIC_APP_URL=https://your-domain.com
```

Never grant credits from the frontend Razorpay Checkout success callback. The
callback is only for UX; the signed webhook is the source of truth.

## Quotas

Current MVP quotas are enforced before upload parsing or translation job
creation:

- Active translation jobs per user: `2`
- Uploads per user per UTC day: `5`
- Lifetime free translation words per user: `2000`

Environment overrides:

```text
MODEL_PROVIDER=gemini
GEMINI_MODEL=gemini/gemini-3.0-flash
TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER=2
TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER=5
TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER=2000
TRANSLATOR_MODEL_TIMEOUT_SECONDS=0
TRANSLATOR_MODEL_RETRIES=0
```

`TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER=-1` disables the lifetime free-word
limit for local/admin use.

`TRANSLATOR_MODEL_TIMEOUT_SECONDS=0` disables the backend's application-level
model timeout. Slow provider calls can still fail upstream, but the API server
will not stop them only because they crossed a local time limit.

When both `GOOGLE_API_KEY` and `NVIDIA_API_KEY` are present and
`MODEL_PROVIDER` is unset, the backend defaults to Gemini. Set
`MODEL_PROVIDER=nvidia` only when we intentionally want to route translations
through NVIDIA NIM.

Queued/running translation jobs are marked failed on backend startup. This avoids
orphaned "running" jobs after a local restart, since in-process background tasks
cannot resume once the server process exits.

## Preview Pagination

The workspace preview is paginated so large documents do not render thousands of
blocks into the browser at once. The endpoint accepts:

```text
GET /documents/{document_id}/preview?offset=0&limit=40
```

`limit` is capped at `100`. The response includes `total_blocks`, `page`,
`page_count`, `has_previous`, and `has_next`. Translation order is still driven
by sections; pagination only controls how much preview text is displayed.

## Frontend Smoke Testing

For safe UI testing without model calls, start the server with:

```text
BACKEND_STORE=json
TRANSLATOR_USE_FAKE_TRANSLATOR=1
```

This enables the dev-only fake translator in `src/translator/services/translation_jobs.py`. It
returns valid translation JSON for testing upload, polling, preview, export,
usage, and responsive UI behavior without spending provider credits.

Quota errors return structured details:

```json
{
  "detail": {
    "message": "Free translation word limit reached.",
    "code": "quota_exceeded",
    "action": "Translate a smaller section or upgrade when billing is available.",
    "quota": {
      "type": "lifetime_free_translation_words",
      "limit_words": 2000,
      "used_words": 1980,
      "requested_words": 50,
      "remaining_words": 20
    }
  }
}
```

Duplicate clicks on a document with an existing active job still return the
active job before quota enforcement, preserving idempotent behavior.

## Error Response Contract

The API now normalizes user-facing errors into this shape:

```json
{
  "detail": {
    "message": "Choose different source and target languages.",
    "code": "same_language_pair",
    "action": "Change either language and try again."
  }
}
```

`src/translator/api/app.py` owns the normalization helpers and FastAPI exception handlers.
Backend routes should avoid returning raw exception strings to customers. Use a
clear `message`, stable `code`, optional `action`, and a small structured object
when useful, such as `quota` or validation `fields`.

Important user-safe codes already covered:

- `unsupported_file_type`, `empty_upload`, `file_too_large`,
  `no_translatable_text`
- `document_not_found`, `job_not_found`, `export_not_found`
- `same_language_pair`, `document_already_translated`,
  `nothing_to_retranslate`, `idempotency_key_conflict`
- `quota_exceeded`, `validation_error`, `authentication_required`

The frontend `formatApiError` in `apps/frontend/app.js` reads this shape and appends
quota/action details into one readable notice. Client-side upload validation
catches unsupported, empty, and over-15 MB files before sending them to the API;
the backend still enforces the same constraints.

## Translation Internals

`POST /documents/{document_id}/translate-next` creates a job record, queues
`backend_api.run_translate_next_job` as a FastAPI background task, and returns:

```json
{
  "job_id": "job_...",
  "status": "queued",
  "poll_url": "/jobs/job_..."
}
```

The background task updates the job to `running`, calls
`backend_translation.translate_next_section`, then stores either the final result
or an error. On success, the job runner also writes one usage ledger record.

`backend_translation.translate_next_section` performs the core sequence:

1. Load parsed document, section plan, existing translations, section records,
   and glossary from the configured document store.
2. Compute `translation_cursor` from consecutively completed sections.
3. Pick `next_section_id`; refuse future arbitrary sections.
4. Convert the selected section into a `TranslationChunk`.
5. Build block prompt payloads with `translation_core.block_items_for_chunk`.
6. Filter relevant glossary with `glossary_for_chunk`.
7. Build the prompt with `translation_core.build_translation_prompt`.
8. Call `TranslationClient.translate`.
9. Parse and safety-normalize JSON with `parse_translation_output`.
10. Save block payloads into `translations.json`.
11. Save the section record into `section_translations.json`.
12. Build glossary prompt, call `TranslationClient.curate_glossary`, merge new
    entries, and save `glossary.json`.

## Job Polling Flow

Frontend flow:

```text
POST /documents/{document_id}/translate-next
-> receive job_id
-> show spinner/progress
-> poll GET /jobs/{job_id}
-> when succeeded, refresh sections and preview
```

If the user clicks translate again while a translation job is queued/running for
the same document, the API returns the existing active job instead of starting a
second model call.

Duplicate click behavior has two layers:

- If a translation job is already `queued` or `running` for the document, the API
  returns that active job with `reused_active_job: true`. This prevents a second
  click during processing from advancing to the next section.
- For duplicate browser submits after the first job already finished, the
  frontend should send a stable `idempotency_key` per button action:

```json
{
  "target_language": "Spanish",
  "idempotency_key": "client-generated-uuid"
}
```

If the same key is received again with the same translate options, the API
returns the original job with `idempotent_replay: true`. If the same key is
reused with different translate options, the API returns `409`.

`POST /documents/{document_id}/translate-rest` uses the same translation and
glossary machinery, but it builds temporary `rest_0001`, `rest_0002`, etc.
chunks from remaining untranslated blocks using a 1500-word target. After each
bulk chunk it saves block translations and glossary updates, so preview/export
state improves incrementally even before the whole job finishes.

## Upload Guardrails

Current MVP upload constraints:

- Allowed extensions: `.txt`, `.docx`, `.epub`.
- Empty uploads are rejected.
- Documents with no translatable text are rejected.
- Maximum upload size defaults to `15728640` bytes and can be changed with
  `TRANSLATOR_MAX_UPLOAD_BYTES`.

These are not a replacement for auth, rate limiting, or quota enforcement; they
are basic safety checks around the local prototype.

## Cost Estimates

Section responses include:

- `estimated_input_tokens`
- `estimated_output_tokens`
- `estimated_prompt_overhead_tokens`
- `estimated_total_tokens`
- `estimated_cost_usd`
- `estimated_cost_per_word_usd`

The current estimate uses one constant blended price:

```text
TRANSLATOR_TOKEN_PRICE_PER_1M_USD=1.0
```

There is no model-selection option in the backend right now.

The estimate intentionally includes a small fixed prompt-overhead token count per
model call. That makes larger 1500-word `translate-rest` chunks cheaper per word
than repeated 600-word section calls, while still using the same token price.

## MySQL Setup

MySQL is the default backend store. The schema lives in
`src/translator/storage/mysql_schema.sql`, and `src/translator/storage/mysql.py` auto-creates the
database/schema by default when the app starts.

Environment variables:

```text
BACKEND_STORE=mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=translator_backend
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_SSL_DISABLED=false
TRANSLATOR_MYSQL_AUTO_CREATE_DATABASE=true
TRANSLATOR_MYSQL_AUTO_INIT_SCHEMA=true
```

Set `BACKEND_STORE=json` to run without MySQL.

## Model Boundary

`backend_translation.TranslationClient` is the abstraction:

```python
async def translate(prompt: str) -> str
async def curate_glossary(prompt: str) -> str
```

`AgentSectionTranslator` is the real implementation using OpenAI Agents +
LiteLLM. Tests use a fake client, so backend tests do not spend model tokens.

## Next Feature Hooks

- Add quota enforcement on top of usage records.
- Move original/export files from local disk to S3/R2 when deploying beyond MVP.
- Replace in-process FastAPI background tasks with a durable queue when deploying
  across multiple workers or machines.
- Add section retranslation/revision policy and decide how to mark later
  sections stale.
- Add cleanup for abandoned uploads and old exports.
