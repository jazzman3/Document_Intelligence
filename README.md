# Document Intelligence — invoice extraction with human review

**Business problem:** Finance and operations teams manually retype invoice fields, and extraction errors contaminate downstream systems. **Target outcome:** Validated structured outputs with visible exceptions and review control.

## Pipeline
Upload text or text-based PDF → SHA-256 deduplication → regex demo extractor or optional LLM JSON extraction → strict Pydantic invoice schema (ISO date, nonnegative amount, two decimals, currency code) → manual approve/reject/correct → SQLite → approved-only CSV export.

## Run
```bash
pip install -r requirements.txt
uvicorn app:app --reload
pytest -q
```
At `/docs` use POST `/documents/text` with `{"name":"invoice.txt","text":"Vendor: Cedar Realty Services\nInvoice Number: CR-1\nDate: 2026-11-05\nTotal: $1250.50"}`; review using returned `id`; download GET `/export.csv`. Invalid documents are never exported. If configuring optional OpenAI, export `OPENAI_API_KEY` first. No OCR is bundled for scanned forms.

## Production roadmap
Add OCR/VLM for image-only PDFs, multi-page tables, line items, tax checks, fraud/supplier validation, bounded queue workers, encryption, tenant isolation, RBAC, review UI, retries, and actual ERP/Sheets API adapters. Do not send confidential invoices to external models without an appropriate contract and security review. Hash-based duplicate detection here uses *text content*, not tenant-scoped storage.

## Evaluation
Use a permission-cleared labeled invoice set (50–100 examples recommended); report per-field exact match, total amount absolute error, schema rejection rate, review rate, end-to-end latency and cost/invoice. Baseline rule extraction and LLM performance must be benchmarked separately. No fabricated scores are provided.
