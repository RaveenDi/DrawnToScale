"""Per-step telemetry and per-run totals. Token counts appear only when the provider returned them;
cost is an ESTIMATE from the config price table and is labelled UNVERIFIED unless that table says otherwise."""
from .config import PRICES_USD_PER_MTOK


def token_counts(usage):
    """(input, output) from an OpenAI-style or input/output-style usage object; None when not returned."""
    if not isinstance(usage, dict):
        return None, None
    pick = lambda *keys: next((usage[k] for k in keys if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)), None)
    return pick("prompt_tokens", "input_tokens"), pick("completion_tokens", "output_tokens")


def cost_basis(model):
    price = PRICES_USD_PER_MTOK.get(model)
    if not price:
        return None
    return "price table (verified)" if price.get("verified") else "UNVERIFIED placeholder prices"


def estimate_cost(model, input_tokens, output_tokens):
    price = PRICES_USD_PER_MTOK.get(model)
    if not price or input_tokens is None or output_tokens is None:
        return None
    return round((input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000, 6)


def normalize(raw, *, model, prompt_version, duration_ms):
    """Canonical per-call telemetry from whatever a provider returned (mock and test doubles return little)."""
    raw = raw or {}
    requested = raw.get("requested_model") or model
    inp, out = token_counts(raw.get("usage"))
    latency = raw.get("latency_ms")
    cost = estimate_cost(requested, inp, out)
    return {"model": raw.get("returned_model") or requested, "prompt_version": raw.get("prompt_version") or prompt_version,
            "input_tokens": inp, "output_tokens": out, "latency_ms": duration_ms if latency is None else latency,
            "est_cost_usd": cost, "cost_basis": cost_basis(requested) if cost is not None else None,
            "model_calls": raw.get("model_calls", 1), "retries": raw.get("retries", 0),
            "repaired": bool(raw.get("repaired", False)), "replayed": bool(raw.get("replayed", False))}


def totals(steps):
    """Run totals from the trace. `tokens_reported` is False when no step returned usage numbers."""
    timed = [s["telemetry"] for s in steps if s.get("telemetry")]
    sums = lambda key: [t[key] for t in timed if t.get(key) is not None]
    tokens_in, tokens_out, costs = sums("input_tokens"), sums("output_tokens"), sums("est_cost_usd")
    bases = {t["cost_basis"] for t in timed if t.get("cost_basis")}
    return {"model_calls": sum(t.get("model_calls", 1) for t in timed),
            "model_turns": sum(1 for s in steps if s.get("kind") == "model_turn"),
            "tool_calls": sum(1 for s in steps if s.get("kind") == "tool_call"),
            "retries": sum(t.get("retries", 0) for t in timed),
            "input_tokens": sum(tokens_in) if tokens_in else None, "output_tokens": sum(tokens_out) if tokens_out else None,
            "tokens_reported": bool(tokens_in or tokens_out),
            "model_latency_ms": sum(sums("latency_ms")), "wall_ms": sum(s.get("duration_ms", 0) for s in steps),
            "est_cost_usd": round(sum(costs), 6) if costs else None,
            "cost_basis": sorted(bases)[0] if len(bases) == 1 else ("mixed" if bases else None)}
