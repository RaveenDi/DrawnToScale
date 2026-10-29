"""Bounded HTTP adapters. All geographic coordinates come from candidate data."""
import base64
from difflib import SequenceMatcher
from io import BytesIO
import json
from pathlib import Path
import time
import unicodedata
import warnings

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .contracts import Area, Candidate, Proposals, SketchGraph
from .llm import (META_URL, AgentTurn, ProviderError, ToolCall, Transport, digest,  # noqa: F401  (re-exported)
                  charge_extra_call, read_json_response, timestamp)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE_URL = "https://www.openstreetmap.org/copyright"
PROMPTS = Path(__file__).parent / "prompts"


def prepare_image(raw):
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise ValueError("Upload a PNG or JPEG up to 8 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG"} or image.width * image.height > 20_000_000:
                    raise ValueError("Use a PNG/JPEG of at most 20 megapixels")
                image.load()
                oriented = ImageOps.exif_transpose(image).convert("RGBA")
                # Flatten transparent sketches onto white and remove EXIF/GPS metadata.
                canvas = Image.new("RGBA", oriented.size, "white")
                canvas.alpha_composite(oriented)
                canvas = canvas.convert("RGB")
                canvas.thumbnail((2048, 2048))
                output = BytesIO()
                canvas.save(output, format="PNG")
                normalized = output.getvalue()
                if len(normalized) > 8 * 1024 * 1024:
                    canvas.thumbnail((1600, 1600))
                    output = BytesIO()
                    canvas.save(output, format="PNG")
                    normalized = output.getvalue()
                return normalized, {"width": canvas.width, "height": canvas.height,
                                    "original_sha256": digest(raw), "sha256": digest(normalized),
                                    "mime": "image/png"}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Cannot decode this PNG/JPEG image safely") from None


class InvalidOutput(Exception):
    """Model content that is complete but not valid JSON / not the schema: one repair attempt is allowed."""

    def __init__(self, summary, raw):
        super().__init__(summary)
        self.summary, self.raw = summary, raw


def parse_structured(contract, response):
    try:
        choice = response["choices"][0]
        raw = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal") or not isinstance(raw, str):
            raise ValueError("Incomplete or refused response")
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("Model output failed schema/semantic validation or was incomplete; review and retry") from None
    try:
        return contract.model_validate_json(raw), raw
    except ValueError as exc:
        errors = getattr(exc, "errors", None)
        found = errors() if callable(errors) else []
        summary = "; ".join(f'{".".join(str(p) for p in e["loc"]) or "output"}: {e["msg"]}' for e in found[:3]) or "not valid JSON"
        raise InvalidOutput(summary[:300], raw[:4000]) from None


def sum_usage(usages):
    """Sum numeric usage fields across a request and its repair; a single response passes through unchanged."""
    usages = [u for u in usages if isinstance(u, dict)]
    if len(usages) < 2:
        return usages[0] if usages else None
    keys = {k for u in usages for k, v in u.items() if isinstance(v, int) and not isinstance(v, bool)}
    return {k: sum(u.get(k, 0) for u in usages if isinstance(u.get(k, 0), int)) for k in sorted(keys)}


class MetaProvider:
    def __init__(self, key="", model="muse-spark-1.3", client=None, transport=None, *,
                 max_completion_tokens=4096, reasoning_effort=None):
        self.key, self.model = key, model
        self.max_completion_tokens, self.reasoning_effort = max_completion_tokens, reasoning_effort  # effort: minimal|low|medium|high|xhigh; "none" is HTTP 400 on Muse Spark
        self.transport = transport or Transport(key, client=client)
        self.client = self.transport.client

    @property
    def configured(self):
        return self.transport.configured

    @property
    def name(self):
        return "replay" if self.transport.mode == "replay" else "meta"

    def _base(self):
        body = {"model": self.model, "max_completion_tokens": self.max_completion_tokens}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def request(self, contract, prompt_name, content):
        if not self.configured:
            raise ProviderError("Set MODEL_API_KEY in project/.env before starting a live run")
        prompt = (PROMPTS / prompt_name).read_text(encoding="utf-8")
        schema = contract.model_json_schema()
        body = {**self._base(), "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": contract.__name__, "strict": True, "schema": schema}}}
        response, info = self.transport.post(body)
        repaired, usages = False, [response.get("usage")]
        while True:
            try:
                value, _ = parse_structured(contract, response)
                break
            except InvalidOutput as bad:
                if repaired:
                    raise ProviderError(f"Model output was still invalid after one repair attempt ({bad.summary}). "
                                        "Nothing was guessed; the run stops here. Review and retry.") from None
                repaired = True
                charge_extra_call()  # the repair is a second billed model call and counts against the run budget
                body = {**body, "messages": [*body["messages"], {"role": "assistant", "content": bad.raw},
                        {"role": "user", "content": f"Your previous reply was invalid: {bad.summary}. Reply again with ONLY the corrected JSON object that matches the schema."}]}
                response, second = self.transport.post(body)
                usages.append(response.get("usage"))
                info = {"latency_ms": (info["latency_ms"] or 0) + (second["latency_ms"] or 0), "retries": info["retries"] + second["retries"],
                        "replayed": info["replayed"] and second["replayed"]}
        return value, {"provider": self.name, "requested_model": self.model,
                       "returned_model": response.get("model"), "response_id": response.get("id"),
                       "usage": sum_usage(usages), "elapsed_seconds": round((info["latency_ms"] or 0) / 1000, 3),
                       "latency_ms": info["latency_ms"], "retries": info["retries"], "replayed": info["replayed"],
                       "repaired": repaired, "model_calls": 2 if repaired else 1,
                       "prompt_version": Path(prompt_name).stem,
                       "prompt_sha256": digest(prompt.encode()),
                       "schema_sha256": digest(json.dumps(schema, sort_keys=True).encode())}

    def extract(self, image, context):
        return self.request(SketchGraph, "extract-v1.md", [
            {"type": "text", "text": context or "Extract only the visible sketch observations."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}},
        ])

    # --- tool-calling turn -------------------------------------------------------------------------
    # Wire shape VERIFIED live 2026-10-30 (tests/fixtures/live/agent-turn-*.json): `tools` are {type:"function",
    # function:{name,description,parameters}}, tool_choice only "auto" (anything else is HTTP 400), the reply has
    # finish_reason "tool_calls" and message.tool_calls[].function.arguments as a JSON *string*, content may be text
    # or null, several calls per turn are normal. Results go back as the full assistant message followed by one
    # {role:"tool", tool_call_id, content:string} per call. Isolated in wire_messages / wire_tools / agent_turn.
    @staticmethod
    def wire_tools(tools):
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in tools]

    @staticmethod
    def wire_messages(system, transcript):
        out = [{"role": "system", "content": system}]
        for m in transcript:
            if m["role"] == "assistant":
                message = {"role": "assistant", "content": m.get("content")}
                if m.get("tool_calls"):
                    message["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                                             for c in m["tool_calls"]]
                out.append(message)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def agent_turn(self, system, transcript, tools):
        if not self.configured:
            raise ProviderError("Set MODEL_API_KEY in project/.env before starting a live run")
        body = {**self._base(), "messages": self.wire_messages(system, transcript),
                "tools": self.wire_tools(tools), "tool_choice": "auto"}
        response, info = self.transport.post(body)
        try:
            choice = response["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") == "length" or message.get("refusal"):
                raise ValueError("Incomplete or refused response")
            calls = []
            for i, raw in enumerate(message.get("tool_calls") or []):
                function = raw.get("function") or {}
                arguments = function.get("arguments", "")
                calls.append(ToolCall(id=str(raw.get("id") or f"call_{i}"), name=str(function.get("name") or ""),
                                      arguments=arguments if isinstance(arguments, str) else json.dumps(arguments)))
            content = message.get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("Model turn was incomplete, refused or had an unexpected tool-call shape; review and retry") from None
        return AgentTurn(content if isinstance(content, str) else None, calls, {
            "provider": self.name, "requested_model": self.model, "returned_model": response.get("model"),
            "response_id": response.get("id"), "usage": response.get("usage"), "latency_ms": info["latency_ms"],
            "retries": info.get("retries", 0), "replayed": info["replayed"], "prompt_version": "match-agent-v1"})

    def match(self, graph, shortlist, hint=None):
        payload = {"sketch": graph.model_dump(), "allowed_candidates_by_sketch_id": shortlist}
        if hint:
            payload["verifier_feedback"] = {"instruction": "Geometry check rejected these earlier picks; choose another allowed ID or null. Only the listed sketch IDs are re-evaluated.",
                                            "rejected_candidate_ids_by_sketch_id": hint["rejected"]}
        return self.request(Proposals, "match-v1.md", json.dumps(payload, ensure_ascii=False))


def normalized_name(value):
    return " ".join("".join(c if c.isalnum() else " " for c in unicodedata.normalize("NFKC", value).casefold()).split())


def rank_candidates(query, candidates, kind=None, limit=6):
    """Deterministic fuzzy name search over Candidate objects (label + aliases), optional type filter."""
    name, wanted = normalized_name(query), normalized_name(kind or "")
    ranked = []
    for candidate in candidates:
        if wanted and wanted not in normalized_name(candidate.type):
            continue
        names = [normalized_name(n) for n in [candidate.label, *candidate.aliases]]
        score = max((SequenceMatcher(None, name, n).ratio() for n in names if name and n), default=0)
        if score >= 0.35:
            ranked.append((score, candidate.id, candidate))
    return [c for _, _, c in sorted(ranked, key=lambda x: (-x[0], x[1]))[:limit]]


def shortlist_candidates(graph, candidates):
    return {landmark.id: [c.model_dump() for c in rank_candidates(landmark.label, candidates)] for landmark in graph.landmarks}


def validate_proposals(graph, proposals, shortlist):
    ids = [p.sketch_id for p in proposals.matches]
    if len(ids) != len(set(ids)) or set(ids) != {p.id for p in graph.landmarks}:
        raise ProviderError("Matching output must include each sketch landmark exactly once")
    for proposal in proposals.matches:
        allowed = {c["id"] for c in shortlist[proposal.sketch_id]}
        if proposal.candidate_id is not None and proposal.candidate_id not in allowed:
            raise ProviderError("Model proposed a geographic ID outside its supplied candidates")


class OSMProvider:
    def __init__(self, cache_dir, client=None, offline=False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline  # replay mode: use any cached snapshot, never the network
        self.client = None if offline else client or httpx.Client(timeout=httpx.Timeout(40, connect=10), follow_redirects=False,
                                            headers={"User-Agent": "DrawnToScale/0.1 (local feasibility prototype)"})

    def retrieve(self, area: Area):
        bbox = f"{area.south},{area.west},{area.north},{area.east}"
        query = f'[out:json][timeout:25][maxsize:16777216];nwr["name"]({bbox});out center tags;'
        cache = self.cache_dir / (digest(query.encode()) + ".json")
        if cache.exists() and (self.offline or time.time() - cache.stat().st_mtime < 86400):
            data = json.loads(cache.read_text(encoding="utf-8"))
            cached = True
        elif self.offline:
            raise ProviderError("Replay mode has no cached geography for this area; run it once in meta mode first")
        else:
            raw = read_json_response(self.client, OVERPASS_URL, limit=8 * 1024 * 1024, data={"data": query})
            if not isinstance(raw, dict) or raw.get("remark") or not isinstance(raw.get("elements"), list):
                raise ProviderError("Geographic retrieval was incomplete; no partial coverage accepted")
            data = {"retrieved_at": timestamp(), "response": raw}
            cached = False
        raw = data["response"]
        if len(raw["elements"]) > 1500:
            raise ProviderError("Too many geographic features; select a smaller area")
        candidates = []
        try:
            for element in raw["elements"]:
                tags = element.get("tags", {})
                label = tags.get("name")
                position = element if element["type"] == "node" else element.get("center", {})
                if not label or "lon" not in position or "lat" not in position:
                    continue
                if not area.contains(position["lon"], position["lat"]):
                    continue
                osm_id = f"{element['type']}/{element['id']}"
                candidates.append(Candidate(id="osm:" + osm_id, label=label,
                    aliases=list(dict.fromkeys(v for k, v in tags.items() if k.startswith("name:") or k in {"alt_name", "short_name"}))[:30],
                    type=next((tags[k] for k in ("amenity", "tourism", "shop", "railway", "leisure", "highway", "place") if k in tags), "landmark"),
                    longitude=position["lon"], latitude=position["lat"],
                    source_url="https://www.openstreetmap.org/" + osm_id,
                    position_kind="node" if element["type"] == "node" else "bounding_box_center"))
        except (ValueError, TypeError, KeyError):
            raise ProviderError("Geographic provider returned invalid feature data") from None
        if len({c.id for c in candidates}) != len(candidates):
            raise ProviderError("Geographic provider returned duplicate IDs")
        if not cached:
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return candidates, {"provider": "OpenStreetMap / Overpass", "attribution": ATTRIBUTION,
            "license_url": LICENSE_URL, "retrieved_at": data["retrieved_at"], "cached": cached,
            "query_sha256": digest(query.encode()),
            "snapshot_sha256": digest(json.dumps(raw, sort_keys=True).encode()),
            "candidate_count": len(candidates),
            "note": "Named features only. Way/relation positions are bounding-box centers, not entrances. Coverage is unverified."}
