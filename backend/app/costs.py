"""Cost accounting. Every request's cost is computed from real token usage and logged,
which powers the daily budget cap and the cost per query numbers on your resume."""
from app.config import get_settings


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
             cache_write_tokens: int = 0) -> float:
    s = get_settings()
    if model == s.small_model:
        inp, out = s.small_input_price, s.small_output_price
    else:
        inp, out = s.large_input_price, s.large_output_price
    # Anthropic bills cache reads at about 10% and cache writes at about 125% of the input rate.
    total = (input_tokens * inp + cache_read_tokens * inp * 0.1 + cache_write_tokens * inp * 1.25
             + output_tokens * out)
    return round(total / 1_000_000, 6)
