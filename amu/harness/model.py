"""Model access for the harness. Bring your own provider: `amu key set --provider …`.
Credential order: ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_BASE_URL env → ~/.amu/credentials.json → `ant auth login` profile.
No credential ⇒ `manual` mode. Secrets are never printed."""

from __future__ import annotations

import json
import time

from amu import keys, outbound, state
from amu.harness import context, providers, schema, validation

DEFAULT_MODEL = "claude-opus-5"


def available() -> bool:
    return keys.available()


def credential() -> dict:
    return keys.credential()


def model_id(repo: str = ".") -> str:
    c = credential()
    return c.get("model") or state.config(repo).get("model") or DEFAULT_MODEL


def client():
    return providers.make_client(credential())


def request_kwargs(cred: dict, effort: str = "medium") -> dict:
    """Anthropic-only parameters are dropped for other families (an OpenAI-compatible endpoint would reject them)."""
    return {"output_config": {"effort": effort}} if providers.supports_anthropic_params(cred) else {}


def _manual(next_: str) -> dict:
    return {"decision": "manual", "confidence": 0.0, "escalate": True, "evidence": [], "next": next_}


def _request_payload(cred: dict, system: str, user: str, selected_model: str) -> dict:
    return dict(model=selected_model, max_tokens=4000, system=system + "\n" + outbound.UNTRUSTED,
                messages=[{"role": "user", "content": user}], **request_kwargs(cred))


def planner_packet(system: str, packet: dict, repo: str) -> str:
    """Reserve prompt/protocol bytes before choosing optional planner rows."""
    cred = credential()
    selected_model = model_id(repo)
    policy = outbound.Policy(repo, [cred.get("api_key")])
    cap = min(context.limits(state.config(repo))["context_bytes"], policy.max_payload)
    overhead = context.estimate(_request_payload(cred, system, "", selected_model))
    budget = min(24000, cap - overhead - 256)
    for _ in range(4):
        if budget <= 0:
            break
        selected = context.encode(context.ranked_slice(packet, budget_bytes=budget))
        size = context.estimate(_request_payload(cred, system, selected, selected_model))
        if size + 128 <= cap:
            return selected
        # JSON context is embedded as a string: quotes/backslashes add bytes.
        # Re-rank from the original packet so omission counts remain accurate.
        budget -= size + 128 - cap
    raise context.ContextLimitError("mandatory planner context exceeds the request budget; narrow the map")


def ask_json(system: str, user: str, model: str | None = None, repo: str = ".") -> dict:
    """One JSON object out; retries once on non-JSON. Never raises on a missing credential."""
    if not available():
        return _manual("run `amu key set` or export ANTHROPIC_API_KEY / GEMINI_API_KEY, or answer by hand")
    cred = credential()
    try:
        if not outbound.Policy(repo).enabled:
            return _manual("model requests disabled by repository outbound policy")
        bounds = context.limits(state.config(repo))
    except (outbound.PolicyError, context.ContextLimitError) as exc:
        return _manual(str(exc))
    c = None
    for attempt in range(2):
        try:
            payload = outbound.Policy(repo, [cred.get("api_key")]).prepare(
                _request_payload(cred, system, user, model or model_id(repo)))
            if context.estimate(payload) > bounds["context_bytes"]:
                return _manual("planner request exceeds the context limit; narrow the map")
            if c is None:
                c = client()
            msg = c.messages.create(**payload)
            validation.message(msg)
        except ImportError:
            return _manual("provider SDK is unavailable; install the model dependencies")
        except outbound.PolicyError as exc:
            return _manual(str(exc))
        except providers.ProviderError as exc:
            if attempt == 0 and exc.retryable:
                time.sleep(0.25)
                continue
            return _manual(f"model call failed ({type(exc).__name__}); answer by hand")
        except validation.ResponseError:
            return _manual("model response was invalid or oversized; review by hand")
        except Exception as exc:  # The Anthropic SDK is optional for OpenAI-compatible providers.
            if cred.get("family") == "anthropic":
                try:
                    import anthropic
                except ImportError:
                    return _manual("pip install anthropic")
                if isinstance(exc, anthropic.AuthenticationError):
                    return _manual("credential rejected: run `amu key set` again")
                if isinstance(exc, (anthropic.APIStatusError, anthropic.APIConnectionError)):
                    return _manual(f"model call failed ({type(exc).__name__}); answer by hand")
            return _manual(f"model call failed ({type(exc).__name__}); answer by hand")
        if msg.stop_reason == "refusal":
            return _manual("model declined this request; answer by hand")
        if msg.stop_reason not in {"end_turn", "stop_sequence"}:
            return _manual("model response was incomplete; review by hand")
        text = "".join(getattr(b, "text", "") for b in msg.content if b.type == "text")
        try:
            return schema.planner_reply(json.loads(text, object_pairs_hook=_unique_object))
        except (ValueError, json.JSONDecodeError):
            user = "Respond with exactly one JSON object and nothing else.\n" + user
    return _manual("model returned invalid planner JSON twice; review by hand")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate planner JSON key")
        result[key] = value
    return result
