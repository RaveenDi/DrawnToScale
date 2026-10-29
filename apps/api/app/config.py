"""Hard caps and price table. Override caps with AGENT_* environment variables (see docs/deployment.md)."""
from dataclasses import dataclass, replace
import os


@dataclass(frozen=True)
class Settings:
    max_steps: int = 8              # model turns in the matching loop
    max_model_calls: int = 16       # per run, cumulative over attempts (extraction + loop turns + JSON repairs)
    max_tool_calls_per_turn: int = 16
    max_tool_calls: int = 64        # per matching loop
    max_invalid_turns: int = 2      # consecutive turns with no usable tool call before the loop abstains
    http_retries: int = 2           # 429 / 5xx / timeout retries per model request
    backoff_s: float = 1.0          # exponential: 1 s, 2 s
    max_retry_wait_s: float = 30.0  # a longer Retry-After than this is not waited out

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        values = {}
        for name, key in (("max_steps", "AGENT_MAX_STEPS"), ("max_model_calls", "AGENT_MAX_MODEL_CALLS"),
                          ("max_tool_calls", "AGENT_MAX_TOOL_CALLS")):
            if env.get(key, "").strip():
                values[name] = int(env[key])
        return replace(cls(), **values)


# USD per million tokens. UNVERIFIED PLACEHOLDERS: not taken from Meta documentation, only here so the
# cost plumbing is exercised. Replace with the published rates before quoting any cost; every report and
# the UI label cost as "estimated, UNVERIFIED prices" while verified is False.
PRICES_USD_PER_MTOK = {
    "muse-spark-1.3": {"input": 1.0, "output": 4.0, "verified": False,
                       "source": "placeholder, not from docs/ or Meta documentation"},
}
