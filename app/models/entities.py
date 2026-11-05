"""SQLAlchemy database models for multi-tenant billing engine."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now():
    return datetime.now(timezone.utc)

def generate_uuid():
    return str(uuid.uuid4())

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    stripe_customer_id = Column(String(255), unique=True, nullable=True, index=True)
    status = Column(String(50), default="active", nullable=False)  # active, past_due, canceled
    created_at = Column(DateTime, default=utc_now, nullable=False)

    subscription = relationship("Subscription", back_populates="tenant", uselist=False)
    usage_events = relationship("UsageEvent", back_populates="tenant")
    invoices = relationship("Invoice", back_populates="tenant")

class Plan(Base):
    __tablename__ = "plans"

    id = Column(String(50), primary_key=True)  # "free", "pro"
    name = Column(String(100), nullable=False)
    base_fee_cents = Column(Integer, default=0, nullable=False)
    api_calls_quota = Column(Integer, default=1000, nullable=False)
    ai_tokens_quota = Column(Integer, default=100000, nullable=False)
    overage_allowed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    subscriptions = relationship("Subscription", back_populates="plan")

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), unique=True, nullable=False, index=True)
    plan_id = Column(String(50), ForeignKey("plans.id"), nullable=False)
    stripe_subscription_id = Column(String(255), unique=True, nullable=True, index=True)
    status = Column(String(50), default="active", nullable=False)  # active, trialing, past_due, canceled
    current_period_start = Column(DateTime, default=utc_now, nullable=False)
    current_period_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    tenant = relationship("Tenant", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")

class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False)
    event_type = Column(String(50), nullable=False)  # "api_call", "ai_tokens"
    quantity = Column(Integer, default=1, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    cached_input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    reasoning_tokens = Column(Integer, default=0, nullable=False)
    cost_microcents = Column(Integer, default=0, nullable=False)
    cost_cents = Column(Integer, default=0, nullable=False)
    response_payload = Column(Text, nullable=True)  # Cache for idempotent response replay
    timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)

    tenant = relationship("Tenant", back_populates="usage_events")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency_key"),
        Index("idx_tenant_event_time", "tenant_id", "timestamp"),
    )

class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"

    event_id = Column(String(255), primary_key=True)
    event_type = Column(String(100), nullable=False)
    processed_at = Column(DateTime, default=utc_now, nullable=False)

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    base_fee_cents = Column(Integer, default=0, nullable=False)
    usage_fee_cents = Column(Integer, default=0, nullable=False)
    total_cents = Column(Integer, default=0, nullable=False)
    currency = Column(String(10), default="usd", nullable=False)
    status = Column(String(50), default="draft", nullable=False)  # draft, open, paid, void
    stripe_invoice_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    tenant = relationship("Tenant", back_populates="invoices")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")

class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    invoice_id = Column(String(36), ForeignKey("invoices.id"), nullable=False, index=True)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    unit_cost_cents = Column(Integer, default=0, nullable=False)
    amount_cents = Column(Integer, default=0, nullable=False)

    invoice = relationship("Invoice", back_populates="line_items")
