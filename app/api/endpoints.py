"""FastAPI routers and endpoint definitions."""
import json
import uuid
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app.models.entities import Tenant, Plan, Subscription, UsageEvent
from app.models.schemas import (
    TenantCreate, TenantResponse,
    UsageEventIngest, UsageEventResponse,
    GenerateRequest, UsageSummaryResponse,
    CheckoutSessionCreate, CheckoutSessionResponse,
    InvoiceResponse
)
from app.services.meter import MeterService
from app.services.quota import QuotaService
from app.services.stripe_sync import StripeSyncService
from app.services.billing import BillingService

api_router = APIRouter()

# --- Customer & Tenant Management ---
@api_router.post("/customers", response_model=TenantResponse, status_code=201)
def create_customer(payload: TenantCreate, db: Session = Depends(get_db)):
    """Creates a new isolated tenant organization with assigned subscription plan."""
    existing = db.query(Tenant).filter(Tenant.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Tenant with email '{payload.email}' already exists")

    plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(status_code=400, detail=f"Invalid plan '{payload.plan_id}'")

    tenant = Tenant(
        name=payload.name,
        email=payload.email,
        status="active"
    )
    db.add(tenant)
    db.flush()

    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30)
    )
    db.add(sub)
    db.commit()
    db.refresh(tenant)

    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        email=tenant.email,
        stripe_customer_id=tenant.stripe_customer_id,
        status=tenant.status,
        plan_id=tenant.subscription.plan_id,
        created_at=tenant.created_at
    )

# --- Usage Metering Ingestion ---
@api_router.post("/events", response_model=UsageEventResponse)
def ingest_event(
    event_data: UsageEventIngest,
    response: Response,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """Ingests a billable activity event with idempotency guarantees.
    
    If header X-Idempotency-Key is provided, it takes precedence.
    Returns:
    - 201 Created: for first-time accepted usage event
    - 200 OK: for duplicate retried request (mirrors cached result)
    - 429 Too Many Requests: if quota exhausted
    - 402 Payment Required: if account unpaid/past_due
    """
    if x_idempotency_key:
        event_data.idempotency_key = x_idempotency_key

    result, is_duplicate = MeterService.record_usage(db, event_data)
    if is_duplicate:
        response.status_code = 200
    else:
        response.status_code = 201
    return result

# --- Billable Dummy AI Generation Endpoint ---
@api_router.post("/generate")
def generate_ai_text(
    request: GenerateRequest,
    response: Response,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    db: Session = Depends(get_db)
):
    """Simulated billable AI execution endpoint exercising quota check, metering, and pricing."""
    event_payload = UsageEventIngest(
        tenant_id=x_tenant_id,
        event_type="ai_tokens",
        idempotency_key=x_idempotency_key,
        input_tokens=request.simulated_input_tokens,
        cached_input_tokens=request.simulated_cached_tokens,
        output_tokens=request.simulated_output_tokens,
        reasoning_tokens=request.simulated_reasoning_tokens
    )

    meter_res, is_duplicate = MeterService.record_usage(db, event_payload)

    # Return simulated LLM response
    return {
        "text": f"Simulated response to '{request.prompt[:30]}...' with {request.simulated_output_tokens} completion tokens.",
        "usage": {
            "input_tokens": request.simulated_input_tokens,
            "cached_input_tokens": request.simulated_cached_tokens,
            "output_tokens": request.simulated_output_tokens,
            "reasoning_tokens": request.simulated_reasoning_tokens,
            "cost_microcents": meter_res.cost_microcents,
            "cost_cents": meter_res.cost_cents,
            "idempotency_cached": is_duplicate
        }
    }

# --- Usage Rollup & Reporting ---
@api_router.get("/customers/{tenant_id}/usage", response_model=UsageSummaryResponse)
@api_router.get("/usage", response_model=UsageSummaryResponse)
def get_usage(
    tenant_id: Optional[str] = None,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    db: Session = Depends(get_db)
):
    """Retrieves usage rollup and real-time costs for the active billing cycle."""
    target_tenant_id = tenant_id or x_tenant_id
    if not target_tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id must be provided in path or X-Tenant-Id header")

    tenant = db.query(Tenant).filter(Tenant.id == target_tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{target_tenant_id}' not found")

    sub = tenant.subscription
    if not sub:
        raise HTTPException(status_code=400, detail="Tenant has no subscription")

    plan = sub.plan
    usage = QuotaService.get_current_period_usage(db, tenant.id, sub.current_period_start)

    api_remaining = max(0, plan.api_calls_quota - usage["api_calls_used"])
    tokens_remaining = max(0, plan.ai_tokens_quota - usage["ai_tokens_used"])
    total_projected = plan.base_fee_cents + usage["usage_cost_cents"]

    return UsageSummaryResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan_id=plan.id,
        subscription_status=sub.status,
        period_start=sub.current_period_start,
        period_end=sub.current_period_end,
        api_calls_used=usage["api_calls_used"],
        api_calls_limit=plan.api_calls_quota,
        api_calls_remaining=api_remaining,
        ai_tokens_used=usage["ai_tokens_used"],
        ai_tokens_limit=plan.ai_tokens_quota,
        ai_tokens_remaining=tokens_remaining,
        input_tokens=usage["input_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        output_tokens=usage["output_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        usage_cost_cents=usage["usage_cost_cents"],
        base_fee_cents=plan.base_fee_cents,
        total_projected_cost_cents=total_projected,
        currency="usd"
    )

# --- Stripe Checkout & Webhook Handling ---
@api_router.post("/checkout/create-session", response_model=CheckoutSessionResponse)
def create_checkout_session(payload: CheckoutSessionCreate, db: Session = Depends(get_db)):
    """Creates a mock / real Stripe Checkout session for upgrade to Pro."""
    tenant = db.query(Tenant).filter(Tenant.id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # In test mode sandbox without live outbound calls, mock standard Stripe session URL
    mock_session_id = f"cs_test_{uuid.uuid4().hex[:18]}"
    checkout_url = f"https://checkout.stripe.com/c/pay/{mock_session_id}"

    return CheckoutSessionResponse(
        session_id=mock_session_id,
        checkout_url=checkout_url,
        tenant_id=tenant.id,
        plan_id="pro"
    )

@api_router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
    db: Session = Depends(get_db)
):
    """Processes verified Stripe Webhooks. Replay protected, forged requests get 400."""
    body_bytes = await request.body()

    # 1. Verify signature
    is_valid = StripeSyncService.verify_webhook_signature(
        payload=body_bytes,
        sig_header=stripe_signature or "",
        secret=settings.STRIPE_WEBHOOK_SECRET
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")

    # 2. Parse and process JSON payload
    try:
        event_dict = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    result = StripeSyncService.process_webhook_event(db, event_dict)
    return result

# --- Invoice Generation ---
@api_router.post("/customers/{tenant_id}/invoices", response_model=InvoiceResponse)
def generate_invoice(tenant_id: str, db: Session = Depends(get_db)):
    """Generates statement invoice for tenant."""
    return BillingService.generate_invoice(db, tenant_id)
