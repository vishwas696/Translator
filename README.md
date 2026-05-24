# Book Translation Agents Starter

Small runnable starter for a book-translation workflow using the OpenAI Agents SDK with LiteLLM-backed models.

The current flow uses a static translation style guide plus three agents:

- `Book Translator` translates each chunk.
- `Glossary Curator` extracts consistency-critical terms from source/translation pairs.
- `Translation Reviewer` reviews the translated sample and supplies concrete feedback.
- Revision pass: `Book Translator` revises each chunk using the original chunk,
  first translation, and reviewer feedback before the final export.

## Setup

1. Add a model provider API key to `.env`.

For NVIDIA NIM:

```env
MODEL_PROVIDER=nvidia
NVIDIA_API_KEY=your-key-here
NVIDIA_MODEL=nvidia_nim/nvidia/nemotron-3-super-120b-a12b
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
```

For Gemini:

```env
MODEL_PROVIDER=gemini
GOOGLE_API_KEY=your-key-here
GEMINI_MODEL=gemini/gemini-3.1-flash-lite
```

2. Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

3. Run the sample:

```powershell
python book_translation_agents.py
```

## Use Your Own Text File

```powershell
python book_translation_agents.py --input .\my_excerpt.txt --target-language Hindi
```

Supported input formats:

- `.docx` - primary supported document format for manuscripts, papers, reports, manuals, and business/legal docs
- `.epub` - primary supported ebook/book format
- `.txt` - experimental format for prompt and chunking tests

PDF is intentionally not supported yet because reliable structure extraction and reconstruction are much harder.

See `DOCUMENT_EDGE_CASES.md` and `DOCX_EPUB_COVERAGE_MATRIX.md` for the current DOCX/EPUB parsing coverage and known reconstruction risks.
The test suite includes `test_edge_case_coverage_matrix.py`, which keeps every
coverage-matrix row tied to either test evidence or an explicit deferred reason.

Use content-form guidance for the high-level structure/purpose, and
document-type guidance for the domain, genre, or tone:

```powershell
python book_translation_agents.py --input .\my_excerpt.txt --target-language English --content-form book --document-type children
```

Supported content forms:

- `book` - books, chapters, manuscripts, EPUB-style narrative or nonfiction
- `article` - standalone articles, essays, news-style pieces, blog-style pieces
- `academic_paper` - scholarly papers, citation-heavy papers, thesis-like documents
- `report` - business reports, white papers, case studies, research reports, technical briefs
- `manual_or_documentation` - manuals, product docs, API docs, how-to guides, SOPs, reference docs
- `legal_or_policy` - contracts, agreements, policies, regulations, standards, government/legal docs

Supported document types include:

- General/form: `general`, `literary`, `commercial_fiction`, `short_story`, `poetry`, `drama`, `screenplay`, `essay`, `humor`, `satire`, `reference`, `dictionary`
- Fiction genres: `adventure`, `fantasy`, `science_fiction`, `dystopian`, `horror`, `thriller`, `mystery`, `crime`, `romance`, `historical_fiction`, `western`, `paranormal`, `magical_realism`, `fairy_tale`, `folktale`, `mythology`
- Audience/story categories: `children`, `middle_grade`, `young_adult`, `new_adult`, `comic`, `graphic_novel`
- Narrative nonfiction: `memoir`, `biography`, `autobiography`, `true_crime`, `travel`, `history`, `journalism`
- Practical nonfiction: `self_help`, `health_wellness`, `parenting`, `lifestyle`, `sports_fitness`, `religion_spirituality`, `philosophy`, `psychology`, `social_science`, `politics`, `economics`, `finance`, `business`, `leadership`, `marketing`, `sales_copy`
- Technical/professional: `technical`, `software_documentation`, `product_documentation`, `user_manual`, `manual`, `academic`, `science`, `popular_science`, `medical`, `legal`, `contract`, `policy_government`
- Education/reports: `textbook`, `educational`, `training_material`, `workbook`, `research_report`, `business_report`, `white_paper`, `case_study`, `grant_proposal`, `presentation`
- Arts/special formats: `art_design`, `architecture`, `photography`, `music`, `cookbook`, `game_rulebook`, `tabletop_rpg`, `email_correspondence`, `personal_letter`

Example combinations:

```powershell
python book_translation_agents.py --input .\novel.epub --target-language Hindi --content-form book --document-type fantasy
python book_translation_agents.py --input .\brief.docx --target-language Hindi --content-form report --document-type technical
python book_translation_agents.py --input .\paper.docx --target-language Hindi --content-form academic_paper --document-type science
python book_translation_agents.py --input .\manual.docx --target-language Hindi --content-form manual_or_documentation --document-type software_documentation
python book_translation_agents.py --input .\agreement.docx --target-language Hindi --content-form legal_or_policy --document-type contract
```

## Chunking

The translator splits text into paragraph-aware chunks before sending it to the model.

```powershell
python book_translation_agents.py --chunk-size 1500 --context-chunks 3
```

- `--chunk-size` sets the maximum source words per chunk.
- `--context-chunks` controls how many previous original/translated chunks are sent as continuity examples.
- `--model-retries` controls how many times transient model/provider failures are retried.
- `--retry-base-delay` controls the first retry delay in seconds; later retries use exponential backoff.
- `--docx2python-enrichment` adds optional `docx2python` run/style diagnostics
  to DOCX block metadata and writes `docx2python_enrichment_report.json`.
  It does not change DOCX write-back; the custom XML parser/writer remains the
  round-trip source of truth.
- Long paragraphs are split at the last sentence boundary that fits the chunk size. If no sentence boundary fits, the chunker falls back to a word boundary and marks the next chunk as a continuation of the same paragraph.
- After each translated chunk, a glossary agent extracts consistency-critical terms. Duplicate source terms are merged, new target variants are added, and the merged glossary is used for later chunks.

To compare the custom DOCX parser against `docx2python` without running a
translation:

```powershell
python compare_docx_parsers.py .\paper.docx --output .\outputs\parser_comparison.json
```

Reviewer feedback is applied by default through a revision pass:

```powershell
python book_translation_agents.py --input .\my_excerpt.docx --target-language Hindi
```

With the revision pass, the script saves first-pass artifacts separately, runs
chunk-level revisions, and writes the revised text to the normal final output
files. For quick/debug runs only, use `--no-revision-pass`.

The script writes generated files into `outputs/`:

- `document_blocks.json`
- `chunk_plan.json`
- `glossary.json`
- `glossary_updates.json`
- `translation_brief.md` with the static style guide used for the run
- `translated_chunks.json`
- `first_pass_translated_chunks.json`, `first_pass_translated_sample.md`, and
  `first_pass_block_translations.json` when the revision pass is enabled
- `revised_chunks.json` and `revision_report.json` when the revision pass is enabled
- `block_translations.json` with the block-id-to-translated-text map used for document export
- `translated_sample.md`
- `translated_document.docx`, `translated_document.epub`, or `translated_document.txt`
- `export_report.json` with block-alignment warnings and modified package files
- `review_report.md`

## Experimental Backend API

The FastAPI backend wraps the existing parser/writer in a document-project API.
It currently supports upload, page-sized section planning, glossary-aware
translation jobs, preview, partial export, and full rest-of-document completion.

Run locally:

```powershell
.\.venv\Scripts\uvicorn.exe backend_api:app --reload
```

Open the integrated frontend at:

```text
http://127.0.0.1:8000/
```

The API uses MySQL by default for document/job/usage state. Original uploads and
exports still stay under `backend_storage/` for now.

```env
BACKEND_STORE=mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=translator_backend
MYSQL_USER=root
MYSQL_PASSWORD=
```

On startup, the backend can create the database and schema from
`backend_mysql_schema.sql`:

```env
TRANSLATOR_MYSQL_AUTO_CREATE_DATABASE=true
TRANSLATOR_MYSQL_AUTO_INIT_SCHEMA=true
```

For quick local testing without MySQL, run with:

```powershell
$env:BACKEND_STORE="json"
.\.venv\Scripts\uvicorn.exe backend_api:app --reload
```

Useful endpoints:

- `POST /documents/upload`
- `GET /documents`
- `GET /documents/{document_id}`
- `GET /documents/{document_id}/sections`
- `GET /documents/{document_id}/glossary`
- `GET /documents/{document_id}/preview`
- `GET /documents/{document_id}/preview?section_id=sec_0001`
- `POST /documents/{document_id}/translate-next`
- `POST /documents/{document_id}/retranslate-last`
- `POST /documents/{document_id}/translate-rest`
- `GET /jobs/{job_id}`
- `GET /usage/me`
- `GET /documents/{document_id}/usage`
- `POST /documents/{document_id}/export`
- `GET /documents/{document_id}/exports/latest/download`

`GET /documents/{document_id}/sections` returns both `next_section_estimate`
for the normal 600-word section flow and `remaining_estimate` for finishing the
rest with 1500-word bulk chunks.

If a translate job is already queued/running, a duplicate click returns that
same active job with `reused_active_job: true`; it will not advance to the next
section. For retries after a job already finished, send an `idempotency_key` in
the JSON body for each button action. Reusing the same key with the same options
returns the original job with `idempotent_replay: true`.

Backend files are stored under `backend_storage/` by default. Override this with
`TRANSLATOR_BACKEND_STORAGE`. Optional MVP guardrail/cost environment variables:

- `TRANSLATOR_MAX_UPLOAD_BYTES` defaults to `15728640`.
- `TRANSLATOR_TOKEN_PRICE_PER_1M_USD` defaults to `1.0`.

Authentication defaults to local dev mode. For production Google Sign-In, set:

```env
TRANSLATOR_AUTH_MODE=google
GOOGLE_OAUTH_CLIENT_ID=your-google-web-client-id.apps.googleusercontent.com
```

Then send the frontend Google ID token on protected backend requests:

```http
Authorization: Bearer <google_id_token>
```

Documents and jobs are owned by the verified Google `sub`; email is stored only
for display/support context.

Successful translation jobs also write estimated usage records. `GET /usage/me`
returns the current user's total estimated words, chunks, tokens, and cost, plus
the underlying records. These estimates use the same constant pricing model as
section estimates and are intended for MVP quotas and billing previews.

Current MVP quota defaults:

- `TRANSLATOR_MAX_ACTIVE_JOBS_PER_USER=2`
- `TRANSLATOR_DAILY_UPLOAD_LIMIT_PER_USER=5`
- `TRANSLATOR_FREE_TRANSLATION_WORDS_PER_USER=2000`

The free translation word quota is lifetime per user, not daily. Set it to `-1`
to disable the limit in local/admin environments.

The DOCX/EPUB exporter currently copies the original package and replaces detected
translatable text sequentially. It preserves package assets and many structural
containers, but exact inline formatting ranges, link ranges, layout overflow, and
full visual fidelity still need later hardening.
