"""Usage metering service with atomic idempotency handling."""
import json
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.models.entities import Tenant, UsageEvent
from app.models.schemas import UsageEventIngest, UsageEventResponse
from app.services.pricing import PricingEngine
from app.services.quota import QuotaService

class MeterService:
    @staticmethod
    def record_usage(db: Session, event_data: UsageEventIngest) -> tuple[UsageEventResponse, bool]:
        """Atomically records a billable event or retrieves the cached response if retried.
        
        Returns:
            (UsageEventResponse, is_duplicate)
        """
        # 1. Fetch Tenant
        tenant = db.query(Tenant).filter(Tenant.id == event_data.tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail=f"Tenant {event_data.tenant_id} not found")

        # 2. Check Idempotency Key first (Fast path lookup)
        existing_event = (
            db.query(UsageEvent)
            .filter(
                UsageEvent.tenant_id == event_data.tenant_id,
                UsageEvent.idempotency_key == event_data.idempotency_key
            )
            .first()
        )
        if existing_event:
            # Replay original result
            resp = UsageEventResponse.model_validate(existing_event)
            resp.is_duplicate = True
            return resp, True

        # 3. Calculate Token sum if AI Tokens
        total_tokens = 0
        if event_data.event_type == "ai_tokens":
            total_tokens = (
                event_data.input_tokens +
                event_data.cached_input_tokens +
                event_data.output_tokens +
                event_data.reasoning_tokens
            )
            if total_tokens == 0 and event_data.quantity > 0:
                total_tokens = event_data.quantity

        # 4. Quota Enforcement check
        QuotaService.check_quota(
            db=db,
            tenant=tenant,
            event_type=event_data.event_type,
            incoming_quantity=event_data.quantity,
            incoming_tokens=total_tokens
        )

        # 5. Calculate Integer Cost
        if event_data.event_type == "ai_tokens":
            cost_microcents, cost_cents = PricingEngine.calculate_token_cost(
                input_tokens=event_data.input_tokens,
                cached_input_tokens=event_data.cached_input_tokens,
                output_tokens=event_data.output_tokens,
                reasoning_tokens=event_data.reasoning_tokens
            )
            quantity = total_tokens
        else:
            cost_microcents, cost_cents = PricingEngine.calculate_api_call_cost(event_data.quantity)
            quantity = event_data.quantity

        # 6. Insert new UsageEvent within transaction boundary
        new_event = UsageEvent(
            tenant_id=tenant.id,
            idempotency_key=event_data.idempotency_key,
            event_type=event_data.event_type,
            quantity=quantity,
            input_tokens=event_data.input_tokens,
            cached_input_tokens=event_data.cached_input_tokens,
            output_tokens=event_data.output_tokens,
            reasoning_tokens=event_data.reasoning_tokens,
            cost_microcents=cost_microcents,
            cost_cents=cost_cents,
        )

        try:
            db.add(new_event)
            db.commit()
            db.refresh(new_event)
        except IntegrityError:
            # Concurrent race condition caught by DB unique constraint
            db.rollback()
            existing = (
                db.query(UsageEvent)
                .filter(
                    UsageEvent.tenant_id == event_data.tenant_id,
                    UsageEvent.idempotency_key == event_data.idempotency_key
                )
                .first()
            )
            if existing:
                resp = UsageEventResponse.model_validate(existing)
                resp.is_duplicate = True
                return resp, True
            raise HTTPException(status_code=500, detail="Database integrity collision during metering")

        resp = UsageEventResponse.model_validate(new_event)
        resp.is_duplicate = False
        return resp, False
