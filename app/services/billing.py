"""Invoice generation and statement service."""
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.entities import Tenant, Subscription, Invoice, InvoiceLineItem
from app.models.schemas import InvoiceResponse
from app.services.quota import QuotaService

class BillingService:
    @staticmethod
    def generate_invoice(db: Session, tenant_id: str) -> InvoiceResponse:
        """Generates itemized invoice for current billing cycle."""
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        sub = tenant.subscription
        if not sub:
            raise HTTPException(status_code=400, detail="Tenant has no subscription")

        plan = sub.plan
        period_start = sub.current_period_start
        period_end = sub.current_period_end

        # Get current period usage
        usage = QuotaService.get_current_period_usage(db, tenant.id, period_start)

        base_fee_cents = plan.base_fee_cents
        usage_fee_cents = usage["usage_cost_cents"]
        total_cents = base_fee_cents + usage_fee_cents

        invoice = Invoice(
            tenant_id=tenant.id,
            period_start=period_start,
            period_end=period_end,
            base_fee_cents=base_fee_cents,
            usage_fee_cents=usage_fee_cents,
            total_cents=total_cents,
            currency="usd",
            status="open"
        )
        db.add(invoice)
        db.flush()

        # Add Line Items
        # 1. Base Subscription Fee
        db.add(InvoiceLineItem(
            invoice_id=invoice.id,
            description=f"Monthly Subscription ({plan.name} Plan)",
            quantity=1,
            unit_cost_cents=base_fee_cents,
            amount_cents=base_fee_cents
        ))

        # 2. API Calls line item
        if usage["api_calls_used"] > 0:
            db.add(InvoiceLineItem(
                invoice_id=invoice.id,
                description=f"API Calls Consumption ({usage['api_calls_used']} calls)",
                quantity=usage["api_calls_used"],
                unit_cost_cents=0,
                amount_cents=0 if plan.id == "free" and usage["api_calls_used"] <= plan.api_calls_quota else 0
            ))

        # 3. AI Tokens line item
        if usage["ai_tokens_used"] > 0:
            db.add(InvoiceLineItem(
                invoice_id=invoice.id,
                description=f"AI Token Usage ({usage['input_tokens']} in, {usage['cached_input_tokens']} cached, {usage['output_tokens']} out, {usage['reasoning_tokens']} reasoning)",
                quantity=usage["ai_tokens_used"],
                unit_cost_cents=0,
                amount_cents=usage["usage_cost_cents"]
            ))

        db.commit()
        db.refresh(invoice)
        return InvoiceResponse.model_validate(invoice)

    @staticmethod
    def list_invoices(db: Session, tenant_id: str):
        """Lists historical statement invoices for a tenant."""
        invoices = db.query(Invoice).filter(Invoice.tenant_id == tenant_id).order_by(Invoice.created_at.desc()).all()
        return [InvoiceResponse.model_validate(inv) for inv in invoices]
