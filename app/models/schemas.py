"""Pydantic schemas for request validation, boundary checks, and API responses."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr, ConfigDict

class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Tenant organization name")
    email: EmailStr = Field(..., description="Tenant billing email")
    plan_id: str = Field(default="free", description="Initial plan (free or pro)")

class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    stripe_customer_id: Optional[str] = None
    status: str
    plan_id: str
    created_at: datetime

class UsageEventIngest(BaseModel):
    tenant_id: str = Field(..., description="Target tenant ID")
    event_type: str = Field(..., pattern="^(api_call|ai_tokens)$", description="Type of usage: api_call or ai_tokens")
    quantity: int = Field(default=1, ge=1, description="Quantity for api_call or general units")
    idempotency_key: str = Field(..., min_length=4, max_length=255, description="Unique client key for deduplication")
    # AI token breakdown (mandatory when event_type == 'ai_tokens')
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)

class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    event_type: str
    quantity: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_microcents: int
    cost_cents: int
    timestamp: datetime
    response_payload: Optional[str] = None
    is_duplicate: bool = False

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Prompt payload for generation")
    simulated_input_tokens: int = Field(default=500, ge=0)
    simulated_cached_tokens: int = Field(default=200, ge=0)
    simulated_output_tokens: int = Field(default=300, ge=0)
    simulated_reasoning_tokens: int = Field(default=100, ge=0)

class UsageSummaryResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    plan_id: str
    subscription_status: str
    period_start: datetime
    period_end: datetime
    # Usage counters
    api_calls_used: int
    api_calls_limit: int
    api_calls_remaining: int
    ai_tokens_used: int
    ai_tokens_limit: int
    ai_tokens_remaining: int
    # Token details
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    # Cost breakdowns
    usage_cost_cents: int
    base_fee_cents: int
    total_projected_cost_cents: int
    currency: str

class CheckoutSessionCreate(BaseModel):
    tenant_id: str = Field(..., description="Tenant upgrading to Pro")
    success_url: Optional[str] = "http://localhost:8000/success"
    cancel_url: Optional[str] = "http://localhost:8000/cancel"

class CheckoutSessionResponse(BaseModel):
    session_id: str
    checkout_url: str
    tenant_id: str
    plan_id: str

class InvoiceLineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    description: str
    quantity: int
    unit_cost_cents: int
    amount_cents: int

class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    period_start: datetime
    period_end: datetime
    base_fee_cents: int
    usage_fee_cents: int
    total_cents: int
    currency: str
    status: str
    stripe_invoice_id: Optional[str] = None
    created_at: datetime
    line_items: List[InvoiceLineItemResponse] = []

class ErrorDetail(BaseModel):
    error: str
    code: str
    message: str
    current_usage: Optional[int] = None
    limit: Optional[int] = None
    reset_at: Optional[datetime] = None
