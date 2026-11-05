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
        # Check if demo tenant exists
        demo_tenant = db.query(Tenant).filter(Tenant.email == "demo@acme-corp.com").first()
        if not demo_tenant:
            demo_tenant = Tenant(
                id="tenant_demo_acme",
                name="Acme Corporation",
                email="demo@acme-corp.com",
                stripe_customer_id="cus_test_acme123",
                status="active"
            )
            db.add(demo_tenant)
            db.flush()

            now = datetime.now(timezone.utc)
            demo_sub = Subscription(
                id="sub_demo_free",
                tenant_id=demo_tenant.id,
                plan_id="free",
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30)
            )
            db.add(demo_sub)
            db.commit()
            print(f"Seeded demo tenant: {demo_tenant.id} ({demo_tenant.name}) on Free plan")
        else:
            print("Demo tenant already exists.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
