"""Database seeding script for clean demonstration and evaluation."""
import uuid
from datetime import datetime, timezone, timedelta
from app.database import SessionLocal, Base, engine
from app.models.entities import Tenant, Plan, Subscription, UsageEvent
from app.main import init_default_plans

def seed_database():
    Base.metadata.create_all(bind=engine)
    init_default_plans()

    db = SessionLocal()
    try:
        tenants_to_seed = [
            ("tenant_001", "Acme Corporation", "demo@acme-corp.com", "cus_test_acme001", "free"),
            ("tenant_002", "Global Tech Labs", "contact@globaltech.io", "cus_test_global002", "pro"),
            ("tenant_003", "Apex Enterprise AI", "billing@apex-ai.net", "cus_test_apex003", "pro"),
            ("tenant_demo_acme", "Acme Demo Sandbox", "sandbox@acme.dev", "cus_test_sandbox", "free"),
        ]

        now = datetime.now(timezone.utc)
        for t_id, name, email, stripe_id, plan_id in tenants_to_seed:
            existing = db.query(Tenant).filter((Tenant.id == t_id) | (Tenant.email == email)).first()
            if not existing:
                tenant = Tenant(
                    id=t_id,
                    name=name,
                    email=email,
                    stripe_customer_id=stripe_id,
                    status="active"
                )
                db.add(tenant)
                db.flush()

                sub = Subscription(
                    id=f"sub_{t_id}",
                    tenant_id=tenant.id,
                    plan_id=plan_id,
                    status="active",
                    current_period_start=now,
                    current_period_end=now + timedelta(days=30)
                )
                db.add(sub)
                db.commit()
                print(f"Seeded tenant: {tenant.id} ({tenant.name}) on {plan_id} plan")
            else:
                print(f"Tenant {t_id} already exists.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
