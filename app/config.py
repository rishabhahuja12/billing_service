"""Application Configuration pinned with pydantic-settings / os."""
import os
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class PricingSettings(BaseModel):
    # Rates defined in micro-cents ($1 = 100 cents = 100,000 micro-cents)
    # per 1,000 tokens (1k units)
    # Gemini 1.5 Flash / Standard Reference Pricing:
    # Fresh input: $0.075 per 1M tokens -> 0.0075 cents / 1k = 7.5 micro-cents / 1k
    # Cached input (50%): 3.75 micro-cents / 1k
    # Output / Reasoning: $0.30 per 1M tokens -> 0.030 cents / 1k = 30.0 micro-cents / 1k
    # Base API call rate: 0.1 cents = 100 micro-cents per call
    INPUT_TOKEN_MICROCENTS_PER_1K: int = 75       # 0.075 cents / 1k tokens = 75 microcents
    CACHED_INPUT_MICROCENTS_PER_1K: int = 38     # discounted 50% = 37.5 microcents ~ 38
    OUTPUT_TOKEN_MICROCENTS_PER_1K: int = 300     # 0.30 cents / 1k tokens = 300 microcents
    API_CALL_MICROCENTS: int = 100               # 0.1 cents per standard API call

    # Tiered pricing thresholds
    FREE_API_CALL_LIMIT: int = 1000
    FREE_AI_TOKEN_LIMIT: int = 100000

    PRO_API_CALL_LIMIT: int = 100000
    PRO_AI_TOKEN_LIMIT: int = 10000000
    PRO_BASE_FEE_CENTS: int = 4900  # $49.00 / month

class Settings(BaseModel):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./billing.db")
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "sk_test_mock_dummy_secret_key")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_mock_test_webhook_secret")
    DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "usd")
    PRICING: PricingSettings = PricingSettings()

settings = Settings()
