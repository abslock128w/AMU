"""Read-only operator diagnostics; readiness requires executed successful probes.

Repository selection here never grants MCP authority. Native graph health checks
remain trusted external programs, not a sandbox. Provider failure details and
model credentials are withheld from the report; model APIs are never contacted.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from itertools import islice
from pathlib import Path

from amu import entire, keys, state, state_io
from amu.outbound import redact

_PASSED = frozenset({"passed", "current"})
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SOURCE = frozenset({"absent", "env", "file", "ant-profile", "not_inspected"})
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEALTH_PASSED = frozenset({"passed", "current", "healthy", "ready", "success", "completed", "available", "ok"})


class DiagnosticInputError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.exit_code = 2
        self.payload = {"error": message, "exit": 2}


def _identity() -> dict:
    from amu.mcp_manifest import runtime_identity

    return runtime_identity()


def _repository_probe(repo: str, provider: dict) -> dict:
    from amu.repository_diagnostics import repo_preflight

    return repo_preflight(repo, provider_probe=lambda: provider)


def _mcp_probe(python: str, repo: str) -> dict:
    from amu.mcp_verification import probe

    return probe(python, repo)


def _row(name: str, passed: bool, *, error_kind: str | None = None, message: str = "", required: bool = True) -> dict:
    value = {"name": name, "required": required, "status": "passed" if passed else "failed", "ready": passed,
             "message": message}
    if error_kind:
        value["error_kind"] = error_kind
    return value


def _text(value: str, secrets=()) -> str:
    # Slice only after redaction: a known key crossing the byte/presentation
    # boundary must not turn into an unrecognizable, disclosed key prefix.
    value = redact(value, secrets)
    value = _URL.sub("[endpoint withheld]", value)
    return "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in value[:2048])


def _clean(value, secrets=(), depth=0):
    if depth > 12:
        return "[diagnostic nesting withheld]"
    if isinstance(value, str):
        return _text(value, secrets)
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, dict):
        return {_text(str(key), secrets): _clean(item, secrets, depth + 1) for key, item in islice(value.items(), 100)
                if not re.search(r"(?:api.?key|password|secret|token|endpoint|base.?url)$", str(key), re.I)}
    if isinstance(value, (list, tuple)):
        return [_clean(item, secrets, depth + 1) for item in value[:100]]
    return "[unsupported diagnostic field withheld]"


def _credential_metadata() -> tuple[dict, tuple[str, ...]]:
    if keys.models_disabled():
        return {"anthropic_key_present": False, "anthropic_key_source": "disabled", "provider": None, "model": None}, ()
    try:
        credential = _credential_record()
        if not isinstance(credential, dict):
            raise ValueError()
        secrets = tuple(value for key, value in credential.items()
                        if key in {"api_key", "base_url"} and isinstance(value, str) and value)
        source = credential.get("source")
        provider = credential.get("provider")
        model = credential.get("model")
        source = source if source in _SOURCE else "unknown"
        provider = provider if isinstance(provider, str) and provider in keys.PROVIDERS else None
        model = (_text(model, secrets) if isinstance(model, str) and _MODEL.fullmatch(model)
                 and not any(secret in model for secret in secrets) else None)
        return {"anthropic_key_present": source != "absent" if source not in {"unknown", "not_inspected"} else None,
                "anthropic_key_source": source, "provider": provider, "model": model}, secrets
    except Exception:
        return {"anthropic_key_present": None, "anthropic_key_source": "not_inspected", "provider": None, "model": None}, ()


def _credential_record() -> dict:
    """Read existing metadata only; never create the home or run auth CLIs."""
    if os.environ.get(keys.ENV):
        return {"provider": "anthropic", "source": "env", "api_key": os.environ[keys.ENV],
                "model": os.environ.get("AMU_MODEL") or "claude-opus-5"}
    gemini_key = os.environ.get(keys.GEMINI_ENV)
    if not gemini_key and os.environ.get("AMU_PROVIDER") == "gemini":
        gemini_key = os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        return {"provider": "gemini", "source": "env", "api_key": gemini_key,
                "model": os.environ.get("AMU_MODEL") or keys.PROVIDERS["gemini"][1]}
    selected = os.environ.get("AMU_PROVIDER")
    if os.environ.get("OPENAI_BASE_URL") or selected in {"openai-compatible", "ollama", "lmstudio", "openrouter"}:
        provider = selected if selected in keys.PROVIDERS else "openai-compatible"
        return {"provider": provider, "source": "env", "api_key": os.environ.get("OPENAI_API_KEY"),
                "model": os.environ.get("AMU_MODEL") or keys.PROVIDERS[provider][1]}
    root = Path(os.environ.get("AMU_HOME") or Path.home() / ".amu")
    saved = _stored_credential(root)
    if isinstance(saved, dict) and saved.get("provider"):
        return {**saved, "source": "file"}
    if isinstance(saved, dict) and saved.get("anthropic_api_key"):
        return {"provider": "anthropic", "source": "file", "api_key": saved["anthropic_api_key"], "model": "claude-opus-5"}
    # Ambient profile detection invokes a separate CLI. Its presence is unknown,
    # not absent, when read-only diagnostics deliberately do not execute it.
    return {"source": "not_inspected"}


def _stored_credential(root: Path) -> dict:
    """Bounded existing 0600 owned single-link credential file, no writes."""
    path = root / "credentials.json"
    maximum = 64 * 1024
    opened = False
    try:
        with state_io._directory(path, root, create=False) as directory:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory.descriptor)
            opened = True
            try:
                before = os.fstat(descriptor)
                state_io._regular(path, before)
                if stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > maximum:
                    raise ValueError("stored credential metadata was not inspected")
                content = bytearray()
                while len(content) <= maximum:
                    chunk = os.read(descriptor, min(65536, maximum + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                if len(content) > maximum:
                    raise ValueError("stored credential metadata was not inspected")
                if (not state._worktree_same_stat(before, os.fstat(descriptor))
                        or not state._worktree_same_stat(before, directory.target(path.name))):
                    raise ValueError("stored credential metadata was not inspected")
                directory.verify()
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        if not opened:
            return {}
        raise ValueError("stored credential metadata was not inspected") from None
    value = json.loads(content.decode("utf-8"), object_pairs_hook=state_io._pairs,
                       parse_float=state_io._float, parse_constant=state_io._constant)
    return value if isinstance(value, dict) else {}


def _config(repo: str) -> dict:
    root = Path(repo) / ".amu"
    return state_io.read(root / "config.json", {}, lambda value: state._validate_saved(root / "config.json", value),
                         managed_root=root)


def _health_failed(value: object) -> bool:
    if not isinstance(value, dict) or not value or value.get("error"):
        return True
    if "rc" in value and (type(value["rc"]) is not int or value["rc"] != 0):
        return True
    if any(value.get(name) is False for name in ("healthy", "ready", "success", "available")):
        return True
    if "status" in value and (not isinstance(value["status"], str) or value["status"].lower() not in _HEALTH_PASSED):
        return True
    checks = value.get("checks")
    if "checks" in value and not isinstance(checks, list):
        return True
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict):
                return True
            if item.get("error") or any(item.get(name) is False for name in ("passed", "healthy", "ready", "success", "available")):
                return True
            if "rc" in item and (type(item["rc"]) is not int or item["rc"] != 0):
                return True
            if "status" in item and (not isinstance(item["status"], str) or item["status"].lower() not in _HEALTH_PASSED):
                return True
            if item.get("required") is True and item.get("status") not in _HEALTH_PASSED:
                return True
    return False


def provider_health_ready(value: object) -> bool:
    """Shared pure health predicate; an executed payload cannot hide failed rc."""
    return not _health_failed(value)


def _bool_probe(probe) -> bool:
    try:
        return probe() is True
    except Exception:
        return False


def _version(probe, secrets=()) -> str:
    try:
        value = probe()
        return _text(value, secrets) if isinstance(value, str) and value.strip() and value != "missing" else "missing"
    except Exception:
        return "missing"


def execute(repo: str, *, mcp: bool = False, mcp_python: str | None = None) -> dict:
    """Collect diagnostics without workflow/global writes or MCP grant changes."""
    if type(mcp) is not bool:
        raise DiagnosticInputError("mcp must be a boolean")
    if mcp_python is not None and not mcp:
        raise DiagnosticInputError("--mcp-python requires --mcp")
    if mcp_python is not None and (not isinstance(mcp_python, str) or not mcp_python.strip() or "\x00" in mcp_python):
        raise DiagnosticInputError("--mcp-python must name the selected Python executable")
    if mcp_python is not None:
        try:
            if len(mcp_python.encode("utf-8")) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in mcp_python):
                raise DiagnosticInputError("--mcp-python must be a bounded executable path without control characters")
        except UnicodeError as exc:
            raise DiagnosticInputError("--mcp-python must be valid UTF-8 text") from exc
    if not isinstance(repo, str) or not repo.strip() or "\x00" in repo:
        raise DiagnosticInputError("repo must be an explicit local directory")
    checks = []
    credential, secrets = _credential_metadata()
    try:
        identity = _identity()
        identity_ready = (isinstance(identity, dict) and isinstance(identity.get("build_sha256"), str)
                          and _SHA256.fullmatch(identity["build_sha256"]) is not None and isinstance(identity.get("amu_version"), str)
                          and bool(identity["amu_version"]) and isinstance(identity.get("executable"), str))
    except Exception:
        identity, identity_ready = {}, False
    checks.append(_row("package_identity", identity_ready, error_kind=None if identity_ready else "identity_unavailable",
                       message="Current package/runtime build identity collected." if identity_ready else "Current package build identity was not established."))
    available = _bool_probe(entire.available)
    graph_available = available and _bool_probe(entire.graph_available)
    version = _version(entire.version, secrets) if available else "missing"
    graph_version = _version(entire.graph_version, secrets) if graph_available else "missing"
    checks.append(_row("entire_availability", available and version != "missing", message="Entire executable/version detected." if available else "Entire executable is unavailable."))
    checks.append(_row("graph_availability", graph_available and graph_version != "missing", message="Graph plugin/version detected." if graph_available else "Graph plugin is unavailable."))
    health = {"error": "graph health probe was not executed", "rc": None}
    health_passed = False
    health_kind = "graph_health_not_run"
    if graph_available:
        try:
            observed = entire.doctor()
            health_passed = provider_health_ready(observed)
            if health_passed:
                health, health_kind = observed, None
            else:
                raw_error = observed.get("error", "") if isinstance(observed, dict) else ""
                health_kind = ("environment_restricted" if isinstance(raw_error, str) and any(
                    term in raw_error.lower() for term in ("permission denied", "operation not permitted", "sandbox", "write-test blocked"))
                    else "graph_health_failed")
                rc = observed.get("rc") if isinstance(observed, dict) else None
                health = {"error": "Graph health probe failed; provider details are withheld.",
                          "rc": rc if type(rc) is int else None, "error_kind": health_kind}
        except Exception:
            health_kind = "graph_health_failed"
            health = {"error": "Graph health probe failed; provider details are withheld.", "error_kind": health_kind}
    health_row = _row("graph_health", health_passed, error_kind=health_kind,
                      message="Graph health probe completed." if health_passed else "Graph health is not established; provider details are withheld.")
    if not graph_available:
        health_row["status"] = "not_run"
    checks.append(health_row)
    provider = {"ready": available and graph_available and version != "missing" and graph_version != "missing" and health_passed}
    try:
        repository = _repository_probe(repo, provider)
        if not isinstance(repository, dict):
            raise ValueError()
        for row in repository.get("checks", []):
            if isinstance(row, dict):
                checks.append(row)
        checks.append(_row("repository_readiness", repository.get("ready") is True,
                           message="Repository prerequisites established." if repository.get("ready") is True else
                           "Repository index/worktree prerequisites are not established; inspect the repository diagnostics."))
    except Exception:
        repository = {"ready": False, "error_kind": "repository_probe_failed", "checks": []}
        checks.append(_row("repository_readiness", False, error_kind="repository_probe_failed", message="Repository diagnostics were not established."))
    try:
        config = _config(repo)
    except Exception:
        config = {}
        checks.append(_row("legacy_config", False, required=False, error_kind="configuration_unreadable", message="Legacy configuration details were withheld."))
    if credential["model"] is None and not keys.models_disabled():
        candidate = config.get("model")
        if isinstance(candidate, str) and _MODEL.fullmatch(candidate) and not any(secret in candidate for secret in secrets):
            credential["model"] = _text(candidate, secrets)
    mcp_result = {"requested": mcp, "status": "not_requested", "repository_allowlist_checked": False,
                  "selected_executable": mcp_python or sys.executable if mcp else None}
    if mcp:
        try:
            # The protocol probe deliberately requires an explicit absolute
            # selection. Resolve relative CLI spelling, not workspace state or
            # live MCP grants, before giving that selection to the helper.
            observed = _mcp_probe(mcp_python or sys.executable, str(Path(repo).absolute()))
            if not isinstance(observed, dict):
                raise ValueError()
            mcp_result.update(observed)
            manifest, protocol, runtime = (observed.get(name) for name in ("manifest", "protocol", "runtime"))
            mcp_passed = (observed.get("status") == "matched" and isinstance(manifest, dict)
                          and manifest.get("match") is True and isinstance(protocol, dict)
                          and protocol.get("initialized") is True and isinstance(runtime, dict)
                          and identity_ready and runtime.get("amu_version") == identity.get("amu_version")
                          and runtime.get("build_sha256") == identity.get("build_sha256"))
        except Exception:
            mcp_result.update(status="failed", error_kind="mcp_probe_failed")
            mcp_passed = False
        checks.append(_row("mcp_inspection_manifest", mcp_passed, error_kind=None if mcp_passed else mcp_result.get("error_kind", "mcp_mismatch"),
                           message="Selected MCP runtime/protocol/inspection manifest matched." if mcp_passed else
                           "Selected MCP runtime/protocol/inspection manifest was not established."))
    ready = all(row.get("status") in _PASSED and row.get("ready", True) is True
                for row in checks if row.get("required") is True)
    # The final repository gate makes informational current_head/operator rows
    # incapable of certifying worktree readiness by accident.
    ready = ready and repository.get("ready") is True
    result = {"schema_version": 1, "ready": ready, "status": "ready" if ready else "not_ready", "exit": 0 if ready else 3,
              "readiness_scope": "Executed local diagnostic prerequisites; not runtime-impact or live MCP-client access certification.",
              "identity": identity, "checks": checks, "repository": repository, "mcp": mcp_result,
              "entire": version, "graph": graph_version, "doctor": health,
              **credential, "unparsed": config.get("unparsed", []) if isinstance(config.get("unparsed", []), list) else [],
              "install": None if available and graph_available else entire.INSTALL_HINT,
              "notes": ["Local CLI repository selection does not establish or expand an MCP server grant.",
                        "MCP protocol verification lists tools only; it does not test actual server repository authorization.",
                        "No project tests/builds/imports, proposed code, or model API requests are executed by these diagnostics.",
                        "Native provider health checks remain trusted external programs; no sandbox guarantee is made."]}
    return _clean(result, secrets)
