# Usage Metering & Billing Engine

Production-grade, zero-loss Usage Metering and Billing Engine built for multi-tenant SaaS architectures. Provides deterministic event deduplication, real-time quota gates, integer-based money math, and cryptographically verified Stripe subscription synchronization.

---

## 1. System Architecture

The service adheres to strict layered domain design separating HTTP transports, core billing logic, and persistence:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HTTP / REST Layer (FastAPI)                     │
│  - Boundary validation & custom 4xx responses                          │
│  - X-Idempotency-Key & Stripe-Signature parsing                        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                             Domain Services                            │
│  ┌───────────────────────┐ ┌──────────────────────┐ ┌────────────────┐ │
│  │   MeterService        │ │    QuotaService      │ │ PricingEngine  │ │
│  │ (Atomic deduplication)│ │  (Pre-action bounds) │ │ (Micro-cents)  │ │
│  └───────────────────────┘ └──────────────────────┘ └────────────────┘ │
│  ┌───────────────────────┐ ┌──────────────────────┐ ┌────────────────┐ │
│  │   BillingService      │ │   StripeSyncService  │ │BackgroundWorker│ │
│  │  (Invoices & Rollup)  │ │ (Webhook HMAC verify)│ │ (Reconcile job)│ │
│  └───────────────────────┘ └──────────────────────┘ └────────────────┘ │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Persistence Layer (SQLAlchemy 2.0)                  │
│   [Tenants]   [Plans]   [Subscriptions]   [UsageEvents]   [Webhooks]   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Capabilities & Architectural Invariants

### 1. Exactly-Once Idempotent Metering
- Every billable operation requires an `idempotency_key` (via HTTP header `X-Idempotency-Key` or request payload).
- A composite unique constraint `(tenant_id, idempotency_key)` guarantees that identical retry requests return the original cached response with `is_duplicate: true` without double-recording usage or billing twice.

### 2. Pre-Action Quota Enforcement & Boundary Honesty
- Quotas are checked **before** fulfilling any billable action.
- When a tenant exhausts their allowance:
  - Returns `HTTP 429 Too Many Requests` with a clear explanation and standard `Retry-After: 3600` header.
- If a tenant account is unpaid or delinquent:
  - Returns `HTTP 402 Payment Required`.

### 3. Integer-Based Zero-Float Money Math
- Storing currency as standard floating-point variables results in accumulation bugs (e.g., IEEE-754 drift).
- All internal calculations are computed in **micro-cents** (`1 cent = 1,000 micro-cents = $0.01`):
  - **Standard Input Tokens:** 75 micro-cents / 1k tokens ($0.075 / 1M).
  - **Cached Input Tokens:** 38 micro-cents / 1k tokens (~50% discount).
  - **Output Tokens:** 300 micro-cents / 1k tokens ($0.30 / 1M).
  - **Reasoning Tokens:** Computed at standard output rate (300 micro-cents / 1k).
  - Final charge is converted to integer cents using `ceil(microcents / 1000)` to eliminate sub-cent loss.

### 4. Cryptographically Verified Stripe Subscription Integration
- Supports Stripe Test Mode workflows (`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`).
- Validates the `Stripe-Signature` using HMAC-SHA256 with timestamp tolerance against replay attacks.
- Deduplicates incoming events via the `processed_webhooks` ledger so replayed webhooks return cleanly without modifying state.

---

## 3. Subscription Plans & Quotas

| Plan Name | Monthly Fee | API Calls Quota | AI Tokens Quota | Overage Allowed |
| :--- | :--- | :--- | :--- | :--- |
| **Free Developer** | $0.00 | 1,000 calls / month | 100,000 tokens / month | No (Hard 429 block) |
| **Pro Organization** | $49.00 / month | 100,000 calls / month | 10,000,000 tokens / month | Yes (Metered overage) |

---

## 4. Quick Start & Execution

### Prerequisites
- Python 3.10+
- SQLite (default) or PostgreSQL

### 1. Installation
```bash
git clone https://github.com/rishabh-ahuja/flyrank-capstone-metering-billing.git
cd flyrank-capstone-metering-billing

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```
*(Ensure `.env` contains test mode keys; defaults work immediately with SQLite)*

### 3. Seed Database
```bash
python -m app.seed
```
*Seeds demo organization `tenant_demo_acme` on the Free plan.*

### 4. Run Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Interactive OpenAPI documentation will be available at: `http://localhost:8000/docs`.

### 5. Run Test Suite
```bash
pytest tests/test_billing_engine.py -v
```

---

## 5. API Reference Summary

- `GET /health` - System health check & background worker status.
- `POST /api/v1/customers` - Register new tenant organization.
- `POST /api/v1/events` - Ingest usage event with idempotency deduplication.
- `POST /api/v1/generate` - Dummy billable AI generation endpoint with token breakdown.
- `GET /api/v1/customers/{tenant_id}/usage` - Real-time aggregated usage rollup and cost calculation.
- `POST /api/v1/checkout/create-session` - Initialize Stripe checkout session.
- `POST /api/v1/webhooks/stripe` - Verified Stripe webhook receiver.
- `POST /api/v1/customers/{tenant_id}/invoices` - Generate itemized monthly invoice.

---

## 6. Honest Limitations & Design Trade-offs
1. **Single-Node Background Task:** The background worker runs as an internal daemon thread. For multi-node distributed deployments, this would transition to Celery with Redis or a temporal workflow orchestrator.
2. **Test Mode Operation:** Payment processing operates strictly against Stripe test mode; real banking rails and live card processing are excluded by design.
