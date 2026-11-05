"""Comprehensive test suite for Usage Metering & Billing Engine.
Verifies all 5 Acceptance Probes from Capstone Brief:
1. Probe 1: Idempotency & exactly-once deduplication under retries
2. Probe 2: Quota boundary honest responses (429 Too Many Requests / 402 Payment Required)
3. Probe 3: Stripe Checkout & webhook subscription upgrade (Free -> Pro with updated quotas)
4. Probe 4: Forged webhook signature rejection (400) and webhook replay deduplication
5. Probe 5: Pinned zero-float AI token pricing rules (cached input, reasoning tokens as output)
"""
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.entities import Tenant, Plan, Subscription, UsageEvent, ProcessedWebhook
from app.services.pricing import PricingEngine
from app.services.stripe_sync import StripeSyncService
from app.config import settings

# In-memory SQLite database isolated for testing with StaticPool to share connection across threads
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=test_engine)
    # Seed plans
    db = TestingSessionLocal()
    try:
        free_plan = Plan(id="free", name="Free Plan", base_fee_cents=0, api_calls_quota=1000, ai_tokens_quota=100000, overage_allowed=False)
        pro_plan = Plan(id="pro", name="Pro Plan", base_fee_cents=4900, api_calls_quota=100000, ai_tokens_quota=10000000, overage_allowed=True)
        db.merge(free_plan)
        db.merge(pro_plan)
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=test_engine)

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def test_tenant(client):
    """Creates a fresh test tenant on Free plan."""
    resp = client.post("/api/v1/customers", json={
        "name": "Test Tenant Org",
        "email": "test@tenant.com",
        "plan_id": "free"
    })
    assert resp.status_code == 201
    return resp.json()

# ==============================================================================
# PROBE 1: IDEMPOTENT USAGE METERING
# ==============================================================================

def test_probe_1_idempotent_event_deduplication(client, test_tenant):
    """Verify sending identical billable request twice with same idempotency key records once."""
    tenant_id = test_tenant["id"]
    idempotency_key = "idemp_test_key_001"

    payload = {
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 10,
        "idempotency_key": idempotency_key
    }

    # First request: should create new event (HTTP 201)
    res1 = client.post("/api/v1/events", json=payload)
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["is_duplicate"] is False
    assert data1["quantity"] == 10
    created_id = data1["id"]

    # Second request (retry): should return existing event without double counting (HTTP 200)
    res2 = client.post("/api/v1/events", json=payload)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["is_duplicate"] is True
    assert data2["id"] == created_id
    assert data2["quantity"] == 10

    # Query usage to prove only 10 API calls recorded, NOT 20
    usage_res = client.get(f"/api/v1/customers/{tenant_id}/usage")
    assert usage_res.status_code == 200
    usage_data = usage_res.json()
    assert usage_data["api_calls_used"] == 10
    assert usage_data["api_calls_remaining"] == 990  # 1000 - 10

def test_probe_1_header_idempotency_key_override(client, test_tenant):
    """Verify X-Idempotency-Key header is respected and deduplicated."""
    tenant_id = test_tenant["id"]
    payload = {
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 5,
        "idempotency_key": "dummy_body_key"
    }
    headers = {"X-Idempotency-Key": "header_key_abc_123"}

    res1 = client.post("/api/v1/events", json=payload, headers=headers)
    assert res1.status_code == 201
    event_id1 = res1.json()["id"]

    # Replay with same header
    res2 = client.post("/api/v1/events", json=payload, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["id"] == event_id1
    assert res2.json()["is_duplicate"] is True

# ==============================================================================
# PROBE 2: QUOTA ENFORCEMENT & BOUNDARY HONESTY (429 / 402)
# ==============================================================================

def test_probe_2_exact_quota_boundary_and_exhaustion(client, test_tenant):
    """Drive tenant to exact quota (1,000 API calls). Next call must fail with 429."""
    tenant_id = test_tenant["id"]

    # Ingest 999 API calls
    res_999 = client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 999,
        "idempotency_key": "step_999"
    })
    assert res_999.status_code == 201

    # Ingest 1 call to hit exactly 1,000 / 1,000
    res_1000 = client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 1,
        "idempotency_key": "step_1000"
    })
    assert res_1000.status_code == 201

    # Verify usage is exactly 1,000
    usage_check = client.get(f"/api/v1/customers/{tenant_id}/usage")
    assert usage_check.json()["api_calls_used"] == 1000
    assert usage_check.json()["api_calls_remaining"] == 0

    # Ingest call 1,001: MUST BE REJECTED with 429 Too Many Requests
    res_exceeded = client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 1,
        "idempotency_key": "step_1001"
    })
    assert res_exceeded.status_code == 429
    err = res_exceeded.json()["detail"]
    assert err["code"] == "API_CALL_QUOTA_EXCEEDED"
    assert "Retry-After" in res_exceeded.headers
    assert err["current_usage"] == 1000
    assert err["limit"] == 1000

def test_probe_2_lapsed_or_unpaid_tenant_returns_402(client, test_tenant):
    """Verify tenant with status 'past_due' returns 402 Payment Required."""
    tenant_id = test_tenant["id"]

    # Update tenant status in database to past_due
    db = TestingSessionLocal()
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    tenant.status = "past_due"
    db.commit()
    db.close()

    res = client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "api_call",
        "quantity": 1,
        "idempotency_key": "blocked_unpaid_call"
    })
    assert res.status_code == 402
    assert res.json()["detail"]["code"] == "SUBSCRIPTION_INACTIVE"

# ==============================================================================
# PROBE 3: STRIPE TEST CHECKOUT & WEBHOOK SUBSCRIPTION UPGRADE
# ==============================================================================

def test_probe_3_checkout_and_webhook_flips_free_to_pro(client, test_tenant):
    """Verify checkout session creation and checkout.session.completed webhook flips tenant to Pro."""
    tenant_id = test_tenant["id"]

    # 1. Initiate checkout session
    checkout_res = client.post("/api/v1/checkout/create-session", json={
        "tenant_id": tenant_id
    })
    assert checkout_res.status_code == 200
    session_data = checkout_res.json()
    assert "https://checkout.stripe.com" in session_data["checkout_url"]

    # 2. Simulate Stripe webhook checkout.session.completed
    webhook_event = {
        "id": "evt_test_checkout_completed_999",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_data["session_id"],
                "customer": "cus_stripe_real_mock_456",
                "client_reference_id": tenant_id,
                "subscription": "sub_stripe_pro_888"
            }
        }
    }
    import json
    payload_bytes = json.dumps(webhook_event).encode("utf-8")
    valid_sig = StripeSyncService.generate_test_signature(payload_bytes, settings.STRIPE_WEBHOOK_SECRET)

    hook_res = client.post(
        "/api/v1/webhooks/stripe",
        content=payload_bytes,
        headers={"Stripe-Signature": valid_sig}
    )
    assert hook_res.status_code == 200
    assert hook_res.json()["status"] == "processed"

    # 3. Verify tenant is now Pro with higher limits (100k API calls, 10M tokens)
    usage_res = client.get(f"/api/v1/customers/{tenant_id}/usage")
    assert usage_res.status_code == 200
    u = usage_res.json()
    assert u["plan_id"] == "pro"
    assert u["api_calls_limit"] == 100000
    assert u["ai_tokens_limit"] == 10000000
    assert u["base_fee_cents"] == 4900

# ==============================================================================
# PROBE 4: FORGED WEBHOOK REJECTION & REPLAY PROTECTION
# ==============================================================================

def test_probe_4_forged_webhook_rejected_with_400(client, test_tenant):
    """Webhook with forged/invalid signature MUST return 400."""
    payload = b'{"id": "evt_fraud_001", "type": "checkout.session.completed"}'
    fake_signature = "t=1700000000,v1=badbadbadbadbadbadbadbadbadbadbadbad"

    res = client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": fake_signature}
    )
    assert res.status_code == 400
    assert "Invalid Stripe webhook signature" in res.json()["detail"]

def test_probe_4_webhook_replay_deduplication(client, test_tenant):
    """Real webhook replayed twice must be processed once and second ignored."""
    webhook_event = {
        "id": "evt_test_replay_protection_002",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_replay",
                "customer": "cus_test",
                "status": "active"
            }
        }
    }
    import json
    payload_bytes = json.dumps(webhook_event).encode("utf-8")
    sig = StripeSyncService.generate_test_signature(payload_bytes, settings.STRIPE_WEBHOOK_SECRET)

    # First delivery: processed
    res1 = client.post("/api/v1/webhooks/stripe", content=payload_bytes, headers={"Stripe-Signature": sig})
    assert res1.status_code == 200
    assert res1.json()["status"] == "processed"

    # Replayed delivery: ignored cleanly
    res2 = client.post("/api/v1/webhooks/stripe", content=payload_bytes, headers={"Stripe-Signature": sig})
    assert res2.status_code == 200
    assert res2.json()["status"] == "ignored"
    assert res2.json()["reason"] == "duplicate_event"

# ==============================================================================
# PROBE 5: AI TOKEN PRICING RULES & ZERO-FLOAT CALCULATIONS
# ==============================================================================

def test_probe_5_token_pricing_rules(client, test_tenant):
    """Verify cached input tokens are cheaper and reasoning tokens are counted as output."""
    tenant_id = test_tenant["id"]

    # Token composition:
    # 10,000 input tokens: 10 * 75 = 750 microcents
    # 20,000 cached tokens: 20 * 38 = 760 microcents (discounted)
    # 5,000 output tokens: 5 * 300 = 1500 microcents
    # 2,000 reasoning tokens: 2 * 300 = 600 microcents (counts as output)
    # Total microcents = 750 + 760 + 1500 + 600 = 3610 microcents -> 4 cents (ceil)
    total_mc, cents = PricingEngine.calculate_token_cost(
        input_tokens=10000,
        cached_input_tokens=20000,
        output_tokens=5000,
        reasoning_tokens=2000
    )
    assert total_mc == 3610
    assert cents == 4

    # Now ingest through API endpoint
    res = client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "ai_tokens",
        "idempotency_key": "probe_5_tokens_event",
        "input_tokens": 10000,
        "cached_input_tokens": 20000,
        "output_tokens": 5000,
        "reasoning_tokens": 2000
    })
    assert res.status_code == 201
    d = res.json()
    assert d["cost_microcents"] == 3610
    assert d["cost_cents"] == 4

    # Verify GET /usage rolls up exactly
    usage_res = client.get(f"/api/v1/customers/{tenant_id}/usage")
    assert usage_res.status_code == 200
    usage_data = usage_res.json()
    assert usage_data["input_tokens"] == 10000
    assert usage_data["cached_input_tokens"] == 20000
    assert usage_data["output_tokens"] == 5000
    assert usage_data["reasoning_tokens"] == 2000
    assert usage_data["ai_tokens_used"] == 37000
    assert usage_data["usage_cost_cents"] == 4

def test_billable_generate_endpoint(client, test_tenant):
    """Test POST /generate endpoint simulating full AI workflow."""
    tenant_id = test_tenant["id"]
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Idempotency-Key": "generate_call_unique_99"
    }
    payload = {
        "prompt": "Summarize quarterly cloud spend report.",
        "simulated_input_tokens": 1000,
        "simulated_cached_tokens": 500,
        "simulated_output_tokens": 200,
        "simulated_reasoning_tokens": 100
    }
    res = client.post("/api/v1/generate", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "Simulated response" in data["text"]
    assert data["usage"]["output_tokens"] == 200
    assert data["usage"]["reasoning_tokens"] == 100

def test_invoice_generation(client, test_tenant):
    """Test generating finalized monthly invoice with itemized line items."""
    tenant_id = test_tenant["id"]

    # Record some usage first
    client.post("/api/v1/events", json={
        "tenant_id": tenant_id,
        "event_type": "ai_tokens",
        "idempotency_key": "inv_usage_1",
        "input_tokens": 50000,
        "cached_input_tokens": 0,
        "output_tokens": 10000,
        "reasoning_tokens": 5000
    })

    inv_res = client.post(f"/api/v1/customers/{tenant_id}/invoices")
    assert inv_res.status_code == 200
    inv = inv_res.json()
    assert inv["tenant_id"] == tenant_id
    assert inv["currency"] == "usd"
    assert len(inv["line_items"]) >= 1
    assert inv["total_cents"] >= inv["usage_fee_cents"]
