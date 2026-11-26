# Greenlight

Spend approval for Orvane Group. Anyone raises a request before they spend, the right people approve it on their phone, and finance can see what has been approved and what has been paid.

## What it does

- **Raise a request** with the supplier, amount in any currency, category, a quote (or the reason there isn't one), and the project or trip it belongs to.
- **Approval route** is worked out from the org chart and the policy:
  - the line manager always approves
  - IT approves anything in an IT category, whatever the amount
  - the COO approves anything at or above the threshold, which can be set per category (travel has its own)
  - one person never approves the same request twice. If Nadia is both the line manager and the COO approver, she clicks once.
- **Recurring spend** (subscriptions, retainers) is approved once for the whole contract value, so nobody re-raises it monthly.
- **Changes after approval**: a lower price keeps the approval and tells finance. A higher price or a new category sends it back through approval. Both versions stay in the history.
- **Cover while away**: an approver names one colleague for a set period. Decisions are recorded as made on the approver's behalf, and nobody can use cover to approve their own request.
- **Finance view**: approved against paid, per request. Payments are recorded against invoices, and overpayment is flagged to the COO.
- **Invoice reading**: finance uploads an invoice, an LLM extracts the fields, and Greenlight suggests which approval it matches. Nothing is recorded until a person confirms.
- **Insights**: spend by project, trip, category and department, with budgets for projects.
- **Audit log**: append-only and hash-chained, so editing any past entry is detectable. The log page verifies the chain.
- **Notifications** by email or Teams, plus daily reminders for approvals left waiting.

## Design

Domain-driven, with the dependency rule pointing inwards:

```
src/greenlight/
  domain/          Pure Python. SpendRequest aggregate, ApprovalPolicy, Cover, visibility, invoice matching
  application/     Use cases (SpendApprovals), read models (SpendQueries), ports, audit chain, event handlers
  infrastructure/  SQLAlchemy imperative mappings, repositories, unit of work, OpenAI reader, SMTP/Teams, seed data
  web/             FastAPI routes, server-rendered templates, sign-in, CSRF and security headers
```

The domain has no framework imports. State changes raise domain events, and one handler turns them into audit entries and notifications, so nothing is audited by hand.

Tests follow the same layers: domain tests with no I/O, use-case tests against in-memory adapters, persistence tests against a real database, and HTTP tests through FastAPI.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m greenlight.cli prepare      # migrate, and load the demo organisation outside production
uvicorn greenlight.web.app:create_app --factory --reload
```

Open http://localhost:8000 and pick who to be. With `SIGN_IN=entra`, people sign in with their Microsoft work account instead and are matched by email.

```bash
pytest            # tests
ruff check .      # lint
alembic check     # migrations match the models
```

## Deploying

The demo runs on Render's free tier (`render.yaml`) with a free Neon Postgres database. `deploy/cloud-run/service.yaml` shows the production shape on Cloud Run with Cloud SQL, Secret Manager and a dedicated service account.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres connection string |
| `SECRET_KEY` | Signs session cookies |
| `BASE_URL` | Public URL, used in links and for secure cookies |
| `SIGN_IN` | `demo` or `entra` |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | Microsoft sign-in |
| `OPENAI_API_KEY` | Turns on invoice reading |
| `SMTP_*`, `TEAMS_WEBHOOK_URL` | Notification delivery |
| `CRON_TOKEN` | Protects `/jobs/reminders` and `/jobs/deliver` |
