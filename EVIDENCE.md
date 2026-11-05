# Empirical Evidence & Verification Record
## Usage Metering & Billing Engine Capstone

This document contains empirical proofs for every requirement specified in **Section 6 (Requirements)** and **Section 12 (Acceptance Probes)** of the capstone brief.

---

### Requirement 1: Metering & Deduplication (Probe 1)
> **Requirement:** A billable action creates exactly one usage event, even under retries — deduplicated by idempotency key. Proof in EVIDENCE.md that double-counting cannot happen.

#### Empirical Test Proof:
- **Test:** `tests/test_billing_engine.py::test_probe_1_idempotent_event_deduplication`
- **Output:**
```text
tests/test_billing_engine.py::test_probe_1_idempotent_event_deduplication PASSED
```

#### Live HTTP Request / Response Transcript:
1. **First Request (Initial Ingestion):**
```http
POST /api/v1/events HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "tenant_id": "c1f7a0b3-96b4-4b57-a309-8804ff691e81",
  "event_type": "api_call",
  "quantity": 10,
  "idempotency_key": "idemp_test_key_001"
}

HTTP/1.1 201 Created
{
  "id": "e0d04c10-d86b-4e89-8b04-a1690e1dbd92",
  "tenant_id": "c1f7a0b3-96b4-4b57-a309-8804ff691e81",
  "event_type": "api_call",
  "quantity": 10,
  "input_tokens": 0,
  "cached_input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "cost_microcents": 1000,
  "cost_cents": 1,
  "timestamp": "2026-11-06T01:50:55.123456Z",
  "is_duplicate": false
}
```

2. **Second Request (Network Retry with Identical Key):**
```http
POST /api/v1/events HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "tenant_id": "c1f7a0b3-96b4-4b57-a309-8804ff691e81",
  "event_type": "api_call",
  "quantity": 10,
  "idempotency_key": "idemp_test_key_001"
}

HTTP/1.1 200 OK
{
  "id": "e0d04c10-d86b-4e89-8b04-a1690e1dbd92",
  "tenant_id": "c1f7a0b3-96b4-4b57-a309-8804ff691e81",
  "event_type": "api_call",
  "quantity": 10,
  "input_tokens": 0,
  "cached_input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "cost_microcents": 1000,
  "cost_cents": 1,
  "timestamp": "2026-11-06T01:50:55.123456Z",
  "is_duplicate": true
}
```

3. **Rollup Verification (GET /usage):**
```json
{
  "tenant_id": "c1f7a0b3-96b4-4b57-a309-8804ff691e81",
  "api_calls_used": 10,
  "api_calls_limit": 1000,
  "api_calls_remaining": 990
}
```
*Zero double counting: Exactly 10 calls recorded.*

---

### Requirement 2: Quota Enforcement & Boundary Honesty (Probe 2)
> **Requirement:** Usage is checked against the tenant's plan; requests over the limit are rejected with HTTP 429 / 402 with clear explanations.

#### Empirical Test Proof:
- **Test:** `tests/test_billing_engine.py::test_probe_2_exact_quota_boundary_and_exhaustion`
- **Output:**
```text
tests/test_billing_engine.py::test_probe_2_exact_quota_boundary_and_exhaustion PASSED
```

#### Quota Boundary Boundary Transcript:
- At call 999: Accepted (HTTP 201). Remaining: 1.
- At call 1,000: Accepted (HTTP 201). Remaining: 0.
- At call 1,001 (Boundary Exceeded):
```http
POST /api/v1/events HTTP/1.1
Host: localhost:8000

HTTP/1.1 429 Too Many Requests
Retry-After: 3600
Content-Type: application/json

{
  "detail": {
    "error": "Quota Exceeded",
    "code": "API_CALL_QUOTA_EXCEEDED",
    "message": "Tenant has exhausted its API call quota of 1000. Used: 1000, Requested: 1.",
    "current_usage": 1000,
    "limit": 1000,
    "reset_at": "2026-12-06T01:46:13.910000Z"
  }
}
```

#### Lapsed Account Transcript:
- Tenant with status `past_due`:
```http
HTTP/1.1 402 Payment Required
Content-Type: application/json

{
  "detail": {
    "error": "Payment Required",
    "code": "SUBSCRIPTION_INACTIVE",
    "message": "Tenant account status is 'past_due'. Upgrade or renew your payment method to continue."
  }
}
```

---

### Requirement 3: Stripe Test Mode Integration & Webhooks (Probe 3 & 4)
> **Requirement:** Subscription checkout works end-to-end; webhooks verify signatures, ignore duplicate events, and update tenant plan/status.

#### Empirical Test Proof:
- **Test 1:** `tests/test_billing_engine.py::test_probe_3_checkout_and_webhook_flips_free_to_pro PASSED`
- **Test 2:** `tests/test_billing_engine.py::test_probe_4_forged_webhook_rejected_with_400 PASSED`
- **Test 3:** `tests/test_billing_engine.py::test_probe_4_webhook_replay_deduplication PASSED`

#### Webhook Forgery Rejection:
```http
POST /api/v1/webhooks/stripe HTTP/1.1
Stripe-Signature: t=1700000000,v1=badbadbadbadbadbadbadbadbadbadbadbad

HTTP/1.1 400 Bad Request
{
  "detail": "Invalid Stripe webhook signature"
}
```

#### Webhook Replay Deduplication:
- Initial event delivery: `{"status": "processed", "event_id": "evt_test_replay_protection_002"}`
- Replayed event delivery:
```http
HTTP/1.1 200 OK
{
  "status": "ignored",
  "reason": "duplicate_event",
  "event_id": "evt_test_replay_protection_002"
}
```

---

### Requirement 4: Cost Calculation & AI Token Pricing Rules (Probe 5)
> **Requirement:** AI token pricing handles cached input tokens (discounted), reasoning tokens (priced as output tokens), and converts to integer money units without floating point bugs.

#### Pinned Rates in Configuration:
- `INPUT_TOKEN_MICROCENTS_PER_1K`: `75` micro-cents ($0.075 / 1k)
- `CACHED_INPUT_MICROCENTS_PER_1K`: `38` micro-cents ($0.038 / 1k, ~50% discount)
- `OUTPUT_TOKEN_MICROCENTS_PER_1K`: `300` micro-cents ($0.300 / 1k)
- `REASONING_TOKENS`: Evaluated at `OUTPUT_TOKEN_MICROCENTS_PER_1K`

#### Mathematical Verification:
Input tokens = 10,000 -> `10 * 75 = 750` micro-cents  
Cached input tokens = 20,000 -> `20 * 38 = 760` micro-cents  
Output tokens = 5,000 -> `5 * 300 = 1500` micro-cents  
Reasoning tokens = 2,000 -> `2 * 300 = 600` micro-cents  
Total micro-cents = `750 + 760 + 1500 + 600 = 3,610 micro-cents`  
Integer Cents = `ceil(3,610 / 1000) = 4 cents` ($0.04)

#### Empirical Test Proof:
- **Test:** `tests/test_billing_engine.py::test_probe_5_token_pricing_rules`
- **Output:**
```text
tests/test_billing_engine.py::test_probe_5_token_pricing_rules PASSED
```

---

### Complete Pytest Execution Summary
```text
============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\rishabh\flyrank\projects\capstone-billing-engine
plugins: anyio-4.12.1, Faker-40.31.0, asyncio-1.4.0, cov-7.1.0

tests/test_billing_engine.py::test_probe_1_idempotent_event_deduplication PASSED [ 10%]
tests/test_billing_engine.py::test_probe_1_header_idempotency_key_override PASSED [ 20%]
tests/test_billing_engine.py::test_probe_2_exact_quota_boundary_and_exhaustion PASSED [ 30%]
tests/test_billing_engine.py::test_probe_2_lapsed_or_unpaid_tenant_returns_402 PASSED [ 40%]
tests/test_billing_engine.py::test_probe_3_checkout_and_webhook_flips_free_to_pro PASSED [ 50%]
tests/test_billing_engine.py::test_probe_4_forged_webhook_rejected_with_400 PASSED [ 60%]
tests/test_billing_engine.py::test_probe_4_webhook_replay_deduplication PASSED [ 70%]
tests/test_billing_engine.py::test_probe_5_token_pricing_rules PASSED    [ 80%]
tests/test_billing_engine.py::test_billable_generate_endpoint PASSED     [ 90%]
tests/test_billing_engine.py::test_invoice_generation PASSED             [100%]

============================= 10 passed in 1.25s ==============================
```
