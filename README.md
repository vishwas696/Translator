# Translator Project

Production-oriented document translation workspace for DOCX, EPUB, and TXT files.

## Repository Layout

```text
.
|-- apps/
|   |-- frontend/          # Browser app served by the FastAPI backend
|   `-- landing/           # Static marketing and policy site
|-- src/translator/
|   |-- api/               # FastAPI app and authentication
|   |-- billing/           # Credit pricing and Razorpay integration
|   |-- cli/               # Console commands
|   |-- documents/         # DOCX/EPUB/TXT parsing, diagnostics, enrichment, export
|   |-- services/          # Translation job orchestration
|   |-- storage/           # Local JSON and MySQL persistence backends
|   `-- translation/       # Chunking, prompts, glossary, reconstruction helpers
|-- tests/                 # Unit and integration-style tests
|-- docs/                  # Architecture notes and document coverage matrices
|-- pyproject.toml         # Package metadata, entry points, pytest config
`-- requirements.txt       # Runtime dependency list
```

Runtime and local-only data are ignored:

- `.env`
- `.venv/`
- `backend_storage/`
- `outputs/`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Create a local `.env` from [.env.example](.env.example), then fill in the values you need. For local JSON storage:

```env
BACKEND_STORE=json
```

For MySQL-backed state:

```env
BACKEND_STORE=mysql
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=translator_backend
MYSQL_USER=root
MYSQL_PASSWORD=
TRANSLATOR_MYSQL_AUTO_CREATE_DATABASE=true
TRANSLATOR_MYSQL_AUTO_INIT_SCHEMA=true
```

For model providers:

```env
MODEL_PROVIDER=gemini
GOOGLE_API_KEY=your-key-here
GEMINI_MODEL=gemini/gemini-3.1-pro
```

```env
MODEL_PROVIDER=nvidia
NVIDIA_API_KEY=your-key-here
NVIDIA_MODEL=nvidia_nim/nvidia/nemotron-3-super-120b-a12b
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
```

## Run

Backend and app frontend:

```powershell
$env:BACKEND_STORE="json"
uvicorn translator.api.app:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Model-free smoke testing:

```powershell
$env:BACKEND_STORE="json"
$env:TRANSLATOR_USE_FAKE_TRANSLATOR="true"
uvicorn translator.api.app:app --reload
```

CLI translation workflow:

```powershell
translator-book --input .\my_document.docx --target-language Hindi
```

DOCX parser diagnostic:

```powershell
translator-compare-docx .\paper.docx --output .\outputs\parser_comparison.json
```

## Tests

```powershell
python -m unittest discover -s tests
```

With the dev extra installed, pytest is configured too:

```powershell
python -m pytest
```

## Notes

- PDF support is intentionally out of scope for now.
- DOCX/EPUB fidelity is tracked in [docs/DOCUMENT_EDGE_CASES.md](docs/DOCUMENT_EDGE_CASES.md) and [docs/DOCX_EPUB_COVERAGE_MATRIX.md](docs/DOCX_EPUB_COVERAGE_MATRIX.md).
- Backend implementation notes live in [docs/backend_notes.md](docs/backend_notes.md).
