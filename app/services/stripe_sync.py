"""Stripe integration and webhook reconciliation service."""
import hmac
import hashlib
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.config import settings
from app.models.entities import Tenant, Plan, Subscription, ProcessedWebhook

class StripeSyncService:
    @staticmethod
    def verify_webhook_signature(payload: bytes, sig_header: str, secret: str) -> bool:
        """Verifies the Stripe-Signature header against the raw request body.
        
        Header format: t=timestamp,v1=signature
        """
        if not sig_header or not secret:
            return False

        try:
            parts = dict(item.strip().split("=", 1) for item in sig_header.split(","))
            timestamp = parts.get("t")
            signature = parts.get("v1")

            if not timestamp or not signature:
                return False

            # Prevent replay attacks older than 5 minutes
            tolerance = 300
            current_time = int(time.time())
            if abs(current_time - int(timestamp)) > tolerance:
                return False

            # Compute expected signature: HMAC-SHA256(secret, f"{timestamp}.{payload_str}")
            signed_payload = f"{timestamp}.".encode("utf-8") + payload
            computed_sig = hmac.new(
                secret.encode("utf-8"),
                signed_payload,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(computed_sig, signature)
        except Exception:
            return False

    @staticmethod
    def generate_test_signature(payload: bytes, secret: str, timestamp: int = None) -> str:
        """Helper to generate valid signature header for testing/CLI replay."""
        if timestamp is None:
            timestamp = int(time.time())
        signed_payload = f"{timestamp}.".encode("utf-8") + payload
        sig = hmac.new(
            secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256
        ).hexdigest()
        return f"t={timestamp},v1={sig}"

    @staticmethod
    def process_webhook_event(db: Session, event_data: dict) -> dict:
        """Processes verified Stripe webhook events idempotently."""
        event_id = event_data.get("id")
        event_type = event_data.get("type")

        if not event_id or not event_type:
            raise HTTPException(status_code=400, detail="Invalid Stripe event payload structure")

        # 1. Deduplication check
        existing = db.query(ProcessedWebhook).filter(ProcessedWebhook.event_id == event_id).first()
        if existing:
            return {"status": "ignored", "reason": "duplicate_event", "event_id": event_id}

        # 2. Process by event type
        obj = event_data.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            # Upgrade tenant plan to Pro
            customer_id = obj.get("customer")
            client_reference_id = obj.get("client_reference_id")  # tenant_id
            subscription_id = obj.get("subscription")

            tenant = None
            if client_reference_id:
                tenant = db.query(Tenant).filter(Tenant.id == client_reference_id).first()
            if not tenant and customer_id:
                tenant = db.query(Tenant).filter(Tenant.stripe_customer_id == customer_id).first()

            if tenant:
                if customer_id:
                    tenant.stripe_customer_id = customer_id
                tenant.status = "active"

                # Update or create subscription
                sub = tenant.subscription
                now = datetime.now(timezone.utc)
                if sub:
                    sub.plan_id = "pro"
                    sub.stripe_subscription_id = subscription_id
                    sub.status = "active"
                    sub.current_period_end = now + timedelta(days=30)
                else:
                    sub = Subscription(
                        tenant_id=tenant.id,
                        plan_id="pro",
                        stripe_subscription_id=subscription_id,
                        status="active",
                        current_period_start=now,
                        current_period_end=now + timedelta(days=30),
                    )
                    db.add(sub)

        elif event_type == "customer.subscription.updated":
            subscription_id = obj.get("id")
            sub_status = obj.get("status", "active")  # active, past_due, canceled
            sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == subscription_id).first()
            if sub:
                sub.status = sub_status
                if sub_status in ["past_due", "canceled"]:
                    sub.tenant.status = sub_status
                else:
                    sub.tenant.status = "active"

        elif event_type == "customer.subscription.deleted":
            subscription_id = obj.get("id")
            sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == subscription_id).first()
            if sub:
                sub.plan_id = "free"
                sub.status = "active"
                sub.stripe_subscription_id = None
                sub.tenant.status = "active"

        # Record event in processed_webhooks
        webhook_record = ProcessedWebhook(event_id=event_id, event_type=event_type)
        db.add(webhook_record)
        db.commit()

        return {"status": "processed", "event_id": event_id, "event_type": event_type}
