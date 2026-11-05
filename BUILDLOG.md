# Build Log & AI Assistance Audit (`BUILDLOG.md`)

**Intern / Author:** Rishabh Ahuja  
**Project:** Usage Metering & Billing Engine Capstone  
**Repository:** `flyrank-capstone-metering-billing`  
**Track:** Backend Engineering Track  

---

### 1. Architectural Strategy & Design Phase
- **Where AI helped:**
  - AI assisted in framing the clean layered architecture (HTTP -> Services -> Persistence) and sketching out the multi-tenant entity relationship model.
  - Formulated the exact HMAC-SHA256 signature algorithm structure matching Stripe's `v1` timestamp header scheme.
- **Where AI was wrong / Suboptimal:**
  - Initial scaffolding suggested floating point rates (e.g. `0.000075` dollars per token) for calculations. Floating-point accumulation introduces known drift errors in accounting (e.g. `0.1 + 0.2 != 0.3`).
  - *Correction Applied:* Refactored the core pricing engine to use integer microcents scaled by 1,000, combined with integer arithmetic and ceiling functions on billing conversions.
- **What was changed:**
  - Explicitly decoupled the dummy execution endpoint (`POST /generate`) so it can simulate LLM token generation numbers directly without requiring paid external LLM provider API keys.

---

### 2. Core Billing Logic & Idempotency Phase
- **Where AI helped:**
  - Suggested using a composite database constraint `UniqueConstraint("tenant_id", "idempotency_key")` in SQLAlchemy to handle race conditions where parallel concurrent retries strike simultaneously.
  - Implemented boundary checks returning `429 Too Many Requests` with a standard `Retry-After` header when quotas are exhausted.
- **Where AI was wrong / Suboptimal:**
  - AI initially tried using Pydantic v1 syntax (`Config.from_orm`), which triggered deprecation warnings in Pydantic v2.
  - During test execution on SQLite in-memory, the default connection pool opened separate in-memory databases per thread/request, leading to `OperationalError: no such table: tenants`.
- **What was changed:**
  - Upgraded Pydantic schemas to modern `ConfigDict(from_attributes=True)` and `model_validate(...)`.
  - Configured `StaticPool` in SQLite memory tests to guarantee unified in-memory persistence across all FastAPI TestClient requests.

---

### 3. Stripe Integration & Webhooks Phase
- **Where AI helped:**
  - Drafted the webhook signature verification logic using constant-time string comparison (`hmac.compare_digest`) to protect against timing attacks.
  - Generated test helpers to compute valid test signatures for mock events.
- **What was changed:**
  - Added replay deduplication through a dedicated `processed_webhooks` table ensuring re-delivered webhooks from Stripe CLI / webhook replays return an acknowledgment with `{"status": "ignored", "reason": "duplicate_event"}` rather than crashing or re-applying billing state.

---

### 4. Background Job & Asynchronous Reconciliation
- **Where AI helped:**
  - Designed an autonomous background worker thread to reconcile tenant account status against active Stripe subscriptions and maintain database consistency.
- **What was changed:**
  - Added health statistics exposing the background worker's status, last run timestamp, and audited count via `GET /health`.

---

### 5. Verification & Code Ownership
Every line of code in this repository has been compiled, inspected, and verified against empirical test runs:
- Zero mocked tests: 10 integration and unit tests execute against live FastAPI endpoints and database transactions.
- Zero floating-point arithmetic in billing calculations.
- Clean repository hygiene: `.env` excluded from version control, safe `.env.example` committed.
