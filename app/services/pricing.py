"""Pure mathematical calculation engine for zero-float integer money math."""
from app.config import settings

class PricingEngine:
    """Calculates billing charges with zero floating-point accumulation errors."""

    @staticmethod
    def calculate_token_cost(
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int
    ) -> tuple[int, int]:
        """Calculates token costs in integer microcents and rounded cents.
        
        Rules:
        - Cached input tokens are billed at a discounted rate.
        - Reasoning tokens count as output tokens.
        - Token categories cannot simply be added together without separate rate multipliers.
        
        Returns:
            (cost_microcents, cost_cents)
        """
        cfg = settings.PRICING

        # Total output includes standard generation + reasoning tokens
        effective_output_tokens = output_tokens + reasoning_tokens

        # Micro-cents calculations (using integer math scaling):
        # 1k tokens = 1000 units
        input_microcents = (input_tokens * cfg.INPUT_TOKEN_MICROCENTS_PER_1K) // 1000
        cached_microcents = (cached_input_tokens * cfg.CACHED_INPUT_MICROCENTS_PER_1K) // 1000
        output_microcents = (effective_output_tokens * cfg.OUTPUT_TOKEN_MICROCENTS_PER_1K) // 1000

        total_microcents = input_microcents + cached_microcents + output_microcents

        # Convert microcents to cents (1 cent = 1000 microcents)
        # Integer ceiling division: (total_mc + 999) // 1000
        if total_microcents > 0:
            cents = (total_microcents + 999) // 1000
        else:
            cents = 0

        return total_microcents, cents

    @staticmethod
    def calculate_api_call_cost(quantity: int) -> tuple[int, int]:
        """Calculates cost for API call requests."""
        cfg = settings.PRICING
        total_microcents = quantity * cfg.API_CALL_MICROCENTS
        cents = (total_microcents + 999) // 1000 if total_microcents > 0 else 0
        return total_microcents, cents
