# System Architecture & Design Specification
## Usage Metering & Billing Engine

**Document Version:** 1.0.0  
**Author:** Rishabh Ahuja  
**Status:** Approved / Phase 1 Gate

---

### 1. Executive Summary & Problem Statement
Every multi-tenant SaaS application requires robust answers to three fundamental questions:
1. **How much has a customer/tenant used?**
2. **Have they exceeded their plan limits?**
3. **How much do they owe based on deterministic, audit-compliant calculations?**

Naive billing implementations frequently suffer from distributed race conditions, floating-point rounding errors (e.g., `$0.10 + $0.20 != $0.30`), and duplicate charge anomalies triggered by unhandled network retries or replayed webhooks.

This system provides a production-grade, zero-loss Usage Metering & Billing Engine with:
- **Strict Idempotency:** Guaranteed exactly-once event recording via deterministic client-provided idempotency keys.
- **Pre-action Quota Enforcement:** Real-time quota validation returning HTTP 429 (quota exhausted) or HTTP 402 (payment/upgrade required) before billable actions are fulfilled.
- **Zero-Float Money Math:** Integer-based accounting in micro-units and cents, preventing arithmetic discrepancies across multi-tiered AI token consumption models.
- **Cryptographically Verified Payment Sync:** Test-mode Stripe integration handling webhooks (`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`) with replay resistance and signature verification.

---

### 2. Multi-Tenant Data Model & Entities

The entity model is designed for strict tenant isolation with indexed lookups on critical query paths:

1. **`tenants`**:
   - `id` (VARCHAR(36), PK): UUID identifier.
   - `name` (VARCHAR(255)): Company or user name.
   - `email` (VARCHAR(255), UNIQUE): Primary contact email.
   - `stripe_customer_id` (VARCHAR(255), UNIQUE, Nullable): Mapped customer reference in Stripe.
   - `status` (VARCHAR(50)): `active`, `past_due`, `canceled`.
   - `created_at` (TIMESTAMP UTC).

2. **`plans`**:
   - `id` (VARCHAR(50), PK): Plan identifier (`free`, `pro`, `enterprise`).
   - `name` (VARCHAR(100)): Display name.
   - `base_fee_cents` (INTEGER): Monthly subscription base charge (e.g., $0 for Free, $4900 for Pro).
   - `api_calls_quota` (INTEGER): Free allowance per billing cycle (e.g., 1,000 for Free, 100,000 for Pro).
   - `ai_tokens_quota` (INTEGER): Free allowance per billing cycle (e.g., 100,000 for Free, 10,000,000 for Pro).
   - `overage_allowed` (BOOLEAN): Whether requests beyond quota are permitted with overage billing (Pro) or blocked (Free).

3. **`subscriptions`**:
   - `id` (VARCHAR(36), PK): Subscription UUID.
   - `tenant_id` (VARCHAR(36), FK -> tenants.id, UNIQUE): One active subscription per tenant.
   - `plan_id` (VARCHAR(50), FK -> plans.id): Current plan reference.
   - `stripe_subscription_id` (VARCHAR(255), Nullable): Stripe subscription ID (`sub_...`).
   - `status` (VARCHAR(50)): `active`, `trialing`, `past_due`, `canceled`.
   - `current_period_start` (TIMESTAMP UTC).
   - `current_period_end` (TIMESTAMP UTC).
   - `created_at` (TIMESTAMP UTC).
   - `updated_at` (TIMESTAMP UTC).

4. **`usage_events`**:
   - `id` (VARCHAR(36), PK): Unique internal event ID.
   - `tenant_id` (VARCHAR(36), FK -> tenants.id, Indexed): Tenant owner.
   - `idempotency_key` (VARCHAR(255), UNIQUE with tenant_id, Indexed): Client deduplication key.
   - `event_type` (VARCHAR(50)): `api_call`, `ai_token_generation`.
   - `quantity` (INTEGER): Total units billed.
   - `properties` (JSON): Metadata including detailed token breakdown (`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`).
   - `cost_cents` (INTEGER): Calculated cost in integer cents.
   - `timestamp` (TIMESTAMP UTC, Indexed): Event occurrence time.

5. **`invoices` & `invoice_line_items`**:
   - Records finalized billing statements, base fees, overage line items, and Stripe invoice sync records.

6. **`processed_webhooks`**:
   - `event_id` (VARCHAR(255), PK): Stripe event ID (`evt_...`) for webhook deduplication.
   - `event_type` (VARCHAR(100)).
   - `processed_at` (TIMESTAMP UTC).

---

### 3. API Surface & Contract

- **`POST /api/v1/events`**:
  - Ingests billable activity.
  - Requires `X-Idempotency-Key` or `idempotency_key` in request body.
  - Validates quota before insertion.
  - Returns HTTP 201 on new creation, HTTP 200 with identical cached payload on retry, HTTP 429 if quota exceeded, HTTP 402 if subscription lapsed.

- **`POST /api/v1/generate` (Dummy Billable Execution Endpoint)**:
  - Simulates an AI generation call consuming input, cached, output, and reasoning tokens.
  - Checks tenant quota, logs usage event idempotently, and responds with generation metadata and remaining quota.

- **`GET /api/v1/customers/{tenant_id}/usage`**:
  - Aggregates usage for the active billing cycle.
  - Returns total API calls, token counts by category, used quota vs limit, and calculated cost rollups in cents.

- **`POST /api/v1/checkout/create-session`**:
  - Initializes Stripe Checkout session for upgrading tenant from Free to Pro.

- **`POST /api/v1/webhooks/stripe`**:
  - Validates `Stripe-Signature` using `STRIPE_WEBHOOK_SECRET`.
  - Replay protection via `processed_webhooks` table.
  - Synchronizes tenant plan and status.

- **`POST /api/v1/customers/{tenant_id}/invoices`**:
  - Generates itemized billing statement for the current cycle with integer math verification.

- **`GET /health`**:
  - Health check endpoint returning database connectivity and service status.

---

### 4. Layer Architecture & Clean Separation

The codebase follows strict layered architecture:
```
┌────────────────────────────────────────────────────────┐
│                   HTTP Layer (FastAPI)                 │
│  - Request Validation (Pydantic v2)                    │
│  - Error Handling & HTTP Status Code Mapping           │
│  - Signature Verification & Idempotency Header Parsing │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                    Service Layer                       │
│  - MeterService (Atomic deduplication & recording)     │
│  - QuotaService (Limit evaluation & pre-action gates)  │
│  - PricingEngine (Integer micro-cents token rules)     │
│  - BillingService (Invoice & statement rollups)        │
│  - StripeSyncService (Webhook reconciliation)          │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│                  Data Access Layer                     │
│  - SQLAlchemy Models & Session Management              │
│  - Tenant-isolated query filters                       │
│  - Transaction demarcation with atomic rollbacks       │
└────────────────────────────────────────────────────────┘
```

---

### 5. AI Token Pricing Constants & Exact Math Specification

Token calculations strictly respect the industry ground rules:
- `cached_input_tokens` are billed at 50% discount compared to standard input tokens.
- `reasoning_tokens` (thinking tokens) count as `output_tokens` and are priced at standard output rates.
- Rates are defined in micro-cents per token to prevent truncation errors before converting to integer cents:
  - Standard Input Rate: `$2.50 per 1M tokens` = `250,000 micro-cents / 1M` = `0.25 micro-cents/token`
  - Cached Input Rate: `$1.25 per 1M tokens` = `125,000 micro-cents / 1M` = `0.125 micro-cents/token`
  - Output / Reasoning Rate: `$10.00 per 1M tokens` = `1,000,000 micro-cents / 1M` = `1.00 micro-cents/token`
  - Total Cost = `ceil((input * 0.25 + cached * 0.125 + (output + reasoning) * 1.0) / 1000)` cents.

---

### 6. Explicit Non-Goals
1. **No Live Payment Execution:** Stripe is operated strictly in Test Mode with test cards (`4242...`). Live credentials and card charging are explicitly excluded.
2. **No Physical LLM Calls:** AI tokens are metered as numerical quantities reported by billable clients; real GPU inference / external model vendor invocation is not part of this metering engine.
