"""FastAPI Application entrypoint with lifecycle, error handling, and routing."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base, SessionLocal
from app.models.entities import Plan
from app.api.endpoints import api_router
from app.services.background import background_worker

def init_default_plans():
    """Initializes standard Free and Pro plan blueprints."""
    db = SessionLocal()
    try:
        free_plan = db.query(Plan).filter(Plan.id == "free").first()
        if not free_plan:
            free_plan = Plan(
                id="free",
                name="Free Developer",
                base_fee_cents=0,
                api_calls_quota=1000,
                ai_tokens_quota=100000,
                overage_allowed=False
            )
            db.add(free_plan)

        pro_plan = db.query(Plan).filter(Plan.id == "pro").first()
        if not pro_plan:
            pro_plan = Plan(
                id="pro",
                name="Pro Organization",
                base_fee_cents=4900,  # $49.00
                api_calls_quota=100000,
                ai_tokens_quota=10000000,
                overage_allowed=True
            )
            db.add(pro_plan)
        db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables and seed default plans
    Base.metadata.create_all(bind=engine)
    init_default_plans()
    background_worker.start()
    yield
    # Shutdown
    background_worker.stop()

app = FastAPI(
    title="Usage Metering & Billing Engine",
    description="Production-grade multi-tenant usage metering, quota enforcement, and zero-float billing service.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global custom exception handler for boundary honesty (clean 4xx, never 500)
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request", "code": "VALIDATION_ERROR", "message": str(exc)}
    )

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "metering-billing-engine",
        "background_worker": background_worker.stats
    }

app.include_router(api_router, prefix="/api/v1")
app.include_router(api_router)
