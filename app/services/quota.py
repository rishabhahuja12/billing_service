"""Quota validation and enforcement service."""
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException
from app.models.entities import Tenant, Subscription, UsageEvent

class QuotaService:
    @staticmethod
    def get_current_period_usage(db: Session, tenant_id: str, period_start: datetime):
        """Aggregate total API calls and total tokens consumed in the current active period."""
        # Total API calls
        api_calls_count = (
            db.query(func.coalesce(func.sum(UsageEvent.quantity), 0))
            .filter(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.event_type == "api_call",
                UsageEvent.timestamp >= period_start
            )
            .scalar()
        ) or 0

        # AI tokens aggregation
        token_stats = (
            db.query(
                func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0).label("reasoning_tokens"),
                func.coalesce(func.sum(UsageEvent.cost_cents), 0).label("cost_cents"),
                func.coalesce(func.sum(UsageEvent.quantity), 0).label("token_quantity"),
            )
            .filter(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.event_type == "ai_tokens",
                UsageEvent.timestamp >= period_start
            )
            .first()
        )

        input_toks = token_stats.input_tokens if token_stats else 0
        cached_toks = token_stats.cached_input_tokens if token_stats else 0
        output_toks = token_stats.output_tokens if token_stats else 0
        reasoning_toks = token_stats.reasoning_tokens if token_stats else 0
        token_cost = token_stats.cost_cents if token_stats else 0
        
        # Total effective tokens = input + cached + output + reasoning
        total_tokens = input_toks + cached_toks + output_toks + reasoning_toks

        # Get API calls cost
        api_calls_cost = (
            db.query(func.coalesce(func.sum(UsageEvent.cost_cents), 0))
            .filter(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.event_type == "api_call",
                UsageEvent.timestamp >= period_start
            )
            .scalar()
        ) or 0

        return {
            "api_calls_used": int(api_calls_count),
            "ai_tokens_used": int(total_tokens),
            "input_tokens": int(input_toks),
            "cached_input_tokens": int(cached_toks),
            "output_tokens": int(output_toks),
            "reasoning_tokens": int(reasoning_toks),
            "usage_cost_cents": int(token_cost + api_calls_cost)
        }

    @staticmethod
    def check_quota(
        db: Session,
        tenant: Tenant,
        event_type: str,
        incoming_quantity: int,
        incoming_tokens: int = 0
    ) -> Subscription:
        """Enforces plan limits before accepting billable action.
        
        Throws:
        - HTTP 402 Payment Required: If tenant status is past_due or canceled.
        - HTTP 429 Too Many Requests: If current + incoming exceeds plan quota and plan does not permit overage.
        """
        if tenant.status in ["past_due", "canceled"]:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Payment Required",
                    "code": "SUBSCRIPTION_INACTIVE",
                    "message": f"Tenant account status is '{tenant.status}'. Upgrade or renew your payment method to continue."
                }
            )

        sub = tenant.subscription
        if not sub or sub.status != "active":
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Payment Required",
                    "code": "NO_ACTIVE_SUBSCRIPTION",
                    "message": "Tenant does not have an active subscription."
                }
            )

        plan = sub.plan
        usage = QuotaService.get_current_period_usage(db, tenant.id, sub.current_period_start)

        if event_type == "api_call":
            projected = usage["api_calls_used"] + incoming_quantity
            if projected > plan.api_calls_quota and not plan.overage_allowed:
                raise HTTPException(
                    status_code=429,
                    headers={"Retry-After": "3600"},
                    detail={
                        "error": "Quota Exceeded",
                        "code": "API_CALL_QUOTA_EXCEEDED",
                        "message": f"Tenant has exhausted its API call quota of {plan.api_calls_quota}. Used: {usage['api_calls_used']}, Requested: {incoming_quantity}.",
                        "current_usage": usage["api_calls_used"],
                        "limit": plan.api_calls_quota,
                        "reset_at": sub.current_period_end.isoformat()
                    }
                )
        elif event_type == "ai_tokens":
            projected = usage["ai_tokens_used"] + incoming_tokens
            if projected > plan.ai_tokens_quota and not plan.overage_allowed:
                raise HTTPException(
                    status_code=429,
                    headers={"Retry-After": "3600"},
                    detail={
                        "error": "Quota Exceeded",
                        "code": "TOKEN_QUOTA_EXCEEDED",
                        "message": f"Tenant has exhausted its AI token quota of {plan.ai_tokens_quota}. Used: {usage['ai_tokens_used']}, Requested: {incoming_tokens}.",
                        "current_usage": usage["ai_tokens_used"],
                        "limit": plan.ai_tokens_quota,
                        "reset_at": sub.current_period_end.isoformat()
                    }
                )

        return sub
