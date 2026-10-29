"""Bounded tool-calling matching agent. The model chooses deterministic tools; the deterministic
Verifier decides; on rejection the model gets the feedback once. Hard caps on turns, model calls,
tool calls; unknown tools and bad arguments come back as tool errors; every turn and tool call is
a trace step. The wire format of tool calling lives in the provider (verified against the live API on 2026-10-30)."""
from dataclasses import dataclass, field
import json
import time

from .config import Settings
from .llm import ProviderError
from .providers import PROMPTS
from .telemetry import normalize
from .tools import TOOL_SPECS, MatchTools

PROMPT_VERSION = "match-agent-v1"
SYSTEM = (PROMPTS / (PROMPT_VERSION + ".md")).read_text(encoding="utf-8")
NUDGE = "Reply with tool calls only. Call propose_match for every landmark (candidate_id null to abstain), then finish()."


@dataclass
class Outcome:
    proposals: list
    status: str            # completed | partial_step_cap | partial_call_cap | stopped_provider_error | abstained_invalid_output
    detail: str
    steps: int
    tool_calls: int
    recovered: bool
    telemetry: list = field(default_factory=list)

    def summary(self):
        return {"status": self.status, "detail": self.detail, "steps": self.steps, "tool_calls": self.tool_calls,
                "recovered": self.recovered, "abstained": [p["sketch_id"] for p in self.proposals if not p["candidate_id"]]}


def clip(value, n=160):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= n else text[:n - 1] + "…"


def describe_result(name, result):
    if not result.get("ok"):
        return "error: " + clip(result.get("error", "unknown"), 140)
    if name == "search_candidates":
        return f'{result["count"]} candidates: ' + clip(", ".join(f'{r["label"]} ({r["id"]})' for r in result["results"]), 120)
    if name == "get_candidate":
        return clip(result["candidate"]["label"] + " " + result["candidate"]["id"])
    if name == "distance_between":
        return f'{result["metres"]} m'
    if name == "propose_match":
        return f'{result["recorded"]["sketch_id"]} -> {result["recorded"]["candidate_id"]}; undecided {result["undecided"]}'
    return result.get("status", "ok")


class MatchAgent:
    def __init__(self, provider, tools: MatchTools, *, settings: Settings, verify, charge, calls_left, record, model):
        """verify(rows) -> verifier result dict (and records its own trace step); charge() counts one model call;
        calls_left() -> remaining model calls for this run; record(step) appends a trace step."""
        self.provider, self.tools, self.settings = provider, tools, settings
        self.verify, self.charge, self.calls_left, self.record, self.model = verify, charge, calls_left, record, model

    def run(self, graph) -> Outcome:
        s, t = self.settings, self.tools
        transcript = [{"role": "user", "content": json.dumps({
            "task": "Match every sketch landmark to a stored candidate (or abstain) using the tools.",
            "sketch": graph.model_dump(), "landmark_ids": t.sketch_ids,
            "limits": {"max_turns": s.max_steps, "max_tool_calls_per_turn": s.max_tool_calls_per_turn}}, ensure_ascii=False)}]
        steps = tool_calls = invalid = 0
        recovered, status, detail, verified, outliers, telemetry = False, "completed", "", None, [], []
        while True:
            if steps >= s.max_steps:
                status, detail = "partial_step_cap", f"Step cap reached after {s.max_steps} model turns; undecided landmarks abstain."
                break
            if self.calls_left() <= 0:
                status, detail = "partial_call_cap", f"Model-call cap ({s.max_model_calls}) for this run reached; undecided landmarks abstain."
                break
            self.charge()
            started = time.monotonic()
            try:
                turn = self.provider.agent_turn(SYSTEM, transcript, TOOL_SPECS)
            except ProviderError as exc:
                self.record(self._turn_step(steps + 1, started, "failed", str(exc), None, 0))
                if steps == 0:
                    raise  # no progress at all: fail the run so it can be retried explicitly
                status, detail = "stopped_provider_error", f"{exc} Landmarks decided so far are kept; the rest abstain."
                break
            steps += 1
            names = [c.name or "?" for c in turn.tool_calls]
            self.record(self._turn_step(steps, started, "ok" if names else "no_tool_call",
                                        ("tool calls: " + ", ".join(names)) if names else "no tool call: " + clip(turn.content or "", 100),
                                        turn.telemetry, len(transcript)))
            telemetry.append(turn.telemetry)
            transcript.append({"role": "assistant", "content": turn.content, "tool_calls": turn.tool_calls})
            if not turn.tool_calls:
                invalid += 1
                if invalid >= s.max_invalid_turns:
                    status, detail = "abstained_invalid_output", "The model returned no usable tool call in consecutive turns; undecided landmarks abstain."
                    break
                transcript.append({"role": "user", "content": NUDGE})
                continue
            usable = False
            for i, call in enumerate(turn.tool_calls):
                if t.finished:
                    result = {"ok": False, "error": "finish() already succeeded this turn; remaining calls were skipped."}
                elif i >= s.max_tool_calls_per_turn or tool_calls >= s.max_tool_calls:
                    result = {"ok": False, "error": "Tool-call limit reached; this call was skipped."}
                else:
                    tool_calls += 1
                    begun = time.monotonic()
                    result = t.call(call.name, call.arguments)
                    self.record(self._tool_step(call, result, begun))
                    usable |= result["ok"]
                    transcript.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(result, ensure_ascii=False)})
                    continue
                self.record(self._tool_step(call, result, time.monotonic()))
                transcript.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(result)})
            invalid = 0 if usable else invalid + 1
            if invalid >= s.max_invalid_turns:
                status, detail = "abstained_invalid_output", "Tool calls kept failing validation in consecutive turns; undecided landmarks abstain."
                break
            if t.finished:
                rows = t.rows()
                verdict = self.verify(rows)
                verified = self._signature(rows)
                outliers = verdict["outliers"] if verdict["status"] == "rejected" else []
                if outliers and not recovered:
                    recovered = True
                    t.reject(outliers)
                    transcript.append({"role": "user", "content": json.dumps({"verifier_feedback": {
                        "instruction": "The deterministic geometry check rejected these picks. Re-decide ONLY these landmarks (rejected ids can no longer be proposed for them), then call finish() again.",
                        "rejected_candidate_ids_by_sketch_id": {x: t.rejected.get(x, []) for x in outliers},
                        "fit_residuals_m": verdict.get("fit_residuals_m"), "turns_left": s.max_steps - steps}}, ensure_ascii=False)})
                    outliers = []
                    continue
                break
        return self._finalize(steps, tool_calls, recovered, status, detail, verified, outliers, telemetry)

    @staticmethod
    def _signature(rows):
        return tuple(sorted((r["sketch_id"], r["candidate_id"]) for r in rows if r["candidate_id"]))

    def _finalize(self, steps, tool_calls, recovered, status, detail, verified, outliers, telemetry):
        t = self.tools
        reason = {"partial_step_cap": "Step cap reached before a decision; left for your decision.",
                  "partial_call_cap": "Model-call cap reached before a decision; left for your decision.",
                  "stopped_provider_error": "The model call failed before a decision; left for your decision.",
                  "abstained_invalid_output": "The model did not produce a usable decision; left for your decision.",
                  "completed": "No decision recorded; left for your decision."}[status]
        rows = []
        for sid in t.sketch_ids:
            if sid in t.proposals:
                rows.append({"sketch_id": sid, **t.proposals[sid]})
            else:
                text = "Verifier rejected the earlier pick and no replacement was chosen; abstaining." if t.rejected.get(sid) else reason
                rows.append({"sketch_id": sid, "candidate_id": None, "evidence": text})
        if self._signature(rows) != verified:  # the loop ended early on a state the Verifier has not seen
            verdict = self.verify(rows)
            outliers = verdict["outliers"] if verdict["status"] == "rejected" else []
        for r in rows:
            if r["sketch_id"] in outliers:
                r.update(candidate_id=None, evidence="Geometry check failed twice; left for your decision." if recovered
                         else "Geometry check rejected this pick and the loop ended before it could be re-matched.")
        if status != "completed":
            self.record({"agent": "Matcher", "kind": "stop", "role": "Matching loop stopped early", "model": "none (deterministic cap)",
                         "input_summary": f"{steps} turns, {tool_calls} tool calls", "output_summary": detail,
                         "duration_ms": 0, "status": "partial" if status.startswith(("partial", "stopped")) else "abstained"})
        elif outliers:
            detail = f"{len(outliers)} landmark(s) abstained: geometry check failed after the one recovery."
        return Outcome(rows, status, detail, steps, tool_calls, recovered, telemetry)

    def _turn_step(self, n, started, status, summary, raw, n_messages):
        duration = round((time.monotonic() - started) * 1000)
        step = {"agent": "Matcher", "kind": "model_turn", "role": f"Model turn {n}/{self.settings.max_steps}: choose tools", "model": self.model,
                "input_summary": f"{n_messages} messages, {len(TOOL_SPECS)} tools", "output_summary": summary,
                "duration_ms": duration, "status": status}
        if raw is not None:
            step["telemetry"] = normalize(raw, model=self.model, prompt_version=PROMPT_VERSION, duration_ms=duration)
        return step

    def _tool_step(self, call, result, begun):
        return {"agent": "Tool", "kind": "tool_call", "tool": call.name or "?", "role": f"Tool call: {call.name or '?'}",
                "model": "none (deterministic tool)", "input_summary": clip(call.arguments or ""),
                "output_summary": describe_result(call.name, result), "duration_ms": round((time.monotonic() - begun) * 1000),
                "status": "ok" if result["ok"] else "tool_error"}
