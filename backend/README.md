# APCOTEX Sales Insights — Backend

Enterprise FastAPI backend for Apcotex Industries Sales Insights platform.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- PostgreSQL + SQLAlchemy 2.0 + Alembic
- Pydantic v2 / Pydantic Settings
- Microsoft Graph (MSAL)
- Pandas + OpenPyXL
- Loguru

## Quick start

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux / macOS

pip install -r requirements.txt

# Ensure PostgreSQL database `apcotex_salesinsights` exists and `.env` is configured.

alembic revision --autogenerate -m "initial_schema"
alembic upgrade head

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger UI: http://localhost:8000/docs

## Environment

Configure secrets in `.env` (never commit secrets). See `.env.example`.

| Variable | Purpose |
|----------|---------|
| `APP_HOST` | Bind address (UAT: `0.0.0.0`) |
| `APP_PORT` | Listen port (UAT: `8000`) |
| `CORS_ORIGINS` | Comma-separated browser origins (include `http://<SERVER-IP>:5173` for UAT) |
| `DATABASE_URL` | PostgreSQL connection string |
| `GRAPH_TENANT_ID` | Azure AD tenant |
| `GRAPH_CLIENT_ID` | App registration client id |
| `GRAPH_CLIENT_SECRET` | App registration secret |
| `GRAPH_MAILBOX` | Shared mailbox to sync |
| `SUPER_ADMIN_USERNAME` / `SUPER_ADMIN_PASSWORD` | Env-only Super Admin |

### UAT start (Windows)

```powershell
alembic upgrade head
python run.py
# or: uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Swagger UI: http://localhost:8000/docs (or http://\<SERVER-IP\>:8000/docs on VPN)

## RBAC (Phase 1)

No JWT yet. Pass role headers for authorization hooks:

- `X-User-Role`: `admin` | `user`
- `X-User-Name`: display name
- `X-User-Id`: optional numeric user id

Admin-only endpoints (users write, master uploads, outlook sync, etc.) require `X-User-Role: admin`.

## Frontend contract routes (`/api/...`)

Prepared for Phase 2 React integration (no frontend changes in Phase 1):

| Frontend service | Backend route |
|------------------|---------------|
| Emails | `GET /api/emails` |
| Consolidated records | `GET /api/consolidated-data/records` |
| Distributor details | `GET /api/consolidated-data/distributors` |
| Reports | `GET /api/reports` |
| Report filters | `GET /api/reports/filter-categories` |
| Audit trail | `GET /api/audit-trail` |
| Visualizations | `GET /api/visualizations/*` |
| Dashboard | `GET /api/dashboard/summary` |
| Outlook sync | `POST /api/outlook/sync` |
| Master data | `POST /api/master-data/*/upload` |

Versioned aliases are also mounted under `/api/v1/...`.

## Architecture

```
app/
  api/v1/endpoints/   # HTTP adapters
  services/           # Business logic
  repositories/       # Data access (Repository Pattern)
  models/             # SQLAlchemy ORM
  schemas/            # Pydantic request/response models
  integrations/       # Graph + Excel
  dependencies/       # DI + RBAC
  middleware/         # Request logging
  exceptions/         # Global error handling
```

## AWS EC2 deployment notes

1. Install Python 3.11+, PostgreSQL client libs, and create a systemd unit for uvicorn / gunicorn.
2. Set `APP_ENV=production`, `DEBUG=false`, and production `CORS_ORIGINS`.
3. Point `DATABASE_URL` at RDS (or local Postgres) and run `alembic upgrade head`.
4. Place Graph secrets in environment / AWS Secrets Manager — never bake into the image.
5. Persist `uploads/`, `downloads/`, and `logs/` on a durable volume.

Example process:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## FG Master product standardization

The FG Master lives only in each environment's PostgreSQL database. It is never
seeded by a migration, packaged in the Docker image, or copied between environments.

### Deploy (each environment)

1. Back up the database (`pg_dump`).
2. Deploy the code, then run `alembic upgrade head` (revision `b8c9d0e1f2a3`). This migration:
   - creates `fg_master_versions`, `fg_master_products`, `product_mapping_history`,
     `product_standardization_snapshots` (empty);
   - adds approval/scope columns to `product_alias_mappings` and marks the existing rows of
     that table `APPROVED` with a scope derived from `distributor_id`;
   - adds three nullable columns to `sales_records`
     (`original_product_name`, `product_standardization_status`, `product_mapping_id`).
     No sales row is updated; NULL means "imported before standardization".
3. Restart the API. Until a master is published, the workbench shows
   "FG Master is not configured" and imports keep the original product names.

### One-time master upload (each environment)

1. Sign in as a Super Admin and open **Master Data**.
2. Upload the FG Master workbook (`.xlsx` / `.xlsm`, max 10 MB). Only the
   `Material Description` column is read; Material, Plant and Valuation Type are ignored.
   The upload fails if that column is missing or appears more than once.
3. Review the valid / duplicate / invalid counts and the replace preview
   (new, removed, unchanged descriptions and mappings that point to removed descriptions).
4. Click **Publish** and confirm. The previous active version becomes `superseded`.

Repeat steps 1–4 in local, UAT and production separately.

### Behaviour

- Matching order: approved distributor mapping → approved global mapping → exact FG Master
  match → high / medium similarity (needs review) → unmapped. Only the first three are
  applied automatically on import.
- Approvals in the Emails workbench (eye icon next to Accuracy) create a distributor
  mapping and, when none exists, a global mapping. An existing global mapping is never
  overwritten; a conflicting correction stays distributor-specific.
- Standardization applies to **new imports only**. Historical sales records are never
  reprocessed or updated. Approve products in the workbench before importing an email so the
  import uses the standardized names.
- Duplicate / incremental detection compares the distributor's original product text, so a
  resent file still matches rows imported before standardization.

## License

Proprietary — Apcotex Industries / internal use.
