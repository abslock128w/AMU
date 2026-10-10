"""Bounded local MCP initialization/list diagnostics, not live-client authority.

Base imports need no MCP/Pydantic. A requested probe launches an isolated client
helper and the explicitly selected Python server, imports the optional SDK only
inside that helper, and calls initialize/tools-list only. It does not install,
index, execute project commands, change client configuration, or invoke tools.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

from amu import mcp_access, mcp_manifest, process

MAX_STDOUT_BYTES = 128 * 1024
MAX_STDERR_BYTES = 64 * 1024
_IDENTITY_MARKER = "AMU_RUNTIME_IDENTITY="
_RUNTIME_SCRIPT = """
import importlib.metadata, importlib.util, json, platform, sys
import amu
spec = importlib.util.find_spec('amu.mcp_server')
data = {'amu_version': amu.__version__, 'version': amu.__version__,
        'python_version': platform.python_version(), 'executable': sys.executable,
        'module': spec.origin if spec is not None else None}
for package in ('amu', 'mcp', 'pydantic'):
    try: data['distribution_version' if package == 'amu' else package + '_version'] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError: data['distribution_version' if package == 'amu' else package + '_version'] = None
print(json.dumps(data, allow_nan=False))
"""


def _failure(kind: str, reason: str, *, status: str = "failed", runtime: dict | None = None, exit_code: int = 3) -> dict:
    return {"schema_version": 1, "status": status, "error_kind": kind, "exit": exit_code,
            "reasons": [reason], "runtime": runtime or {}, "manifest": {"match": False},
            "protocol": {"initialized": False, "tool_names": [], "tool_count": 0},
            "authority": {"profile": "inspection", "repository_authorization_certified": False,
                          "live_client_configuration_changed": False, "tool_calls_executed": False}}


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError("duplicate JSON keys")
        result[key] = value
    return result


def _decode(data: bytes) -> dict:
    if len(data) > MAX_STDOUT_BYTES:
        raise ValueError("diagnostic response exceeds byte allowance")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _raw: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("diagnostic response must be an object")
    return value


def _runtime(python: str, timeout: int) -> dict:
    result = process.run([python, "-I", "-B", "-c", _RUNTIME_SCRIPT], timeout=min(timeout, 10),
                         stdout_limit=MAX_STDOUT_BYTES, stderr_limit=MAX_STDERR_BYTES,
                         env=mcp_access.child_environment())
    if result.error or result.returncode:
        raise ValueError("selected Python cannot load the AMU installation")
    runtime = _decode(result.stdout)
    module = runtime.get("module")
    if not isinstance(module, str) or not module or not Path(module).is_file():
        raise ValueError("selected Python does not provide an AMU MCP server")
    build = mcp_manifest.package_build(Path(module).parent)
    runtime.update(build_sha256=build["sha256"], build=build)
    return runtime


def _initialize_identity(hello: dict) -> dict | None:
    instructions = hello.get("instructions")
    if not isinstance(instructions, str):
        return None
    matches = [line[len(_IDENTITY_MARKER):] for line in instructions.splitlines() if line.startswith(_IDENTITY_MARKER)]
    if len(matches) != 1:
        return None
    try:
        return _decode(matches[0].encode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return None


async def _wire(python: str, repo: str, timeout: int) -> dict:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return _failure("mcp_sdk_unavailable", "Optional MCP SDK is unavailable in the diagnostic client runtime.", status="unavailable")
    try:
        runtime = _runtime(python, timeout)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return _failure("mcp_runtime_unavailable", "Selected Python cannot load a bounded AMU MCP installation.", status="unavailable")
    if runtime.get("mcp_version") is None:
        return _failure("mcp_sdk_unavailable", "Selected server Python has no optional MCP SDK; install its matching optional extra explicitly.",
                        status="unavailable", runtime=runtime)
    parameters = StdioServerParameters(command=python, args=["-I", "-B", "-m", "amu.mcp_server", "--allow-repo", repo],
                                       cwd=repo, env=mcp_access.child_environment())
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            hello = (await session.initialize()).model_dump(by_alias=True)
            listed = (await session.list_tools()).model_dump(by_alias=True)
    tools = listed.get("tools")
    comparison = mcp_manifest.compare_tools(tools)
    names = sorted(tool.get("name", "") for tool in tools) if isinstance(tools, list) and all(isinstance(tool, dict) for tool in tools) else []
    identity = _initialize_identity(hello)
    reasons = []
    if not comparison["match"]:
        reasons.append("Advertised tools/schemas differ from the reviewed inspection manifest; use the matching installed candidate and restart the selected server.")
    if identity is None:
        reasons.append("Server initialize metadata lacks AMU manifest/build identity; the selected installation may predate parity diagnostics.")
    else:
        if identity.get("manifest_sha256") != mcp_manifest.inspection_manifest()["sha256"]:
            reasons.append("Server-reported manifest identity differs from the reviewed contract.")
        if identity.get("build_sha256") is None or identity.get("build_sha256") != runtime.get("build_sha256"):
            reasons.append("Server-reported build identity is unavailable or does not match the selected installed payload.")
        if identity.get("amu_version") != runtime.get("amu_version"):
            reasons.append("Server-reported AMU version differs from the selected installed payload.")
    if runtime.get("build_sha256") is None:
        reasons.append("Selected Python payload identity could not be captured completely.")
    source = mcp_manifest.runtime_identity()
    if runtime.get("amu_version") != source["amu_version"] or runtime.get("build_sha256") != source["build_sha256"]:
        reasons.append("Selected server differs from this diagnostic runtime; version strings alone cannot establish installation parity.")
    server_info = hello.get("serverInfo") or {}
    if not server_info.get("version"):
        reasons.append("Server initialize version is missing; old same-version installations cannot be distinguished by version alone.")
    elif identity is not None:
        try:
            if server_info["version"] != mcp_manifest.server_version(identity):
                reasons.append("Server initialize version does not match its reported manifest/build identity.")
        except (KeyError, TypeError):
            reasons.append("Server initialize identity is missing version, manifest or build fields.")
    return {"schema_version": 1, "status": "matched" if not reasons else "mismatch", "exit": 0 if not reasons else 2,
            "error_kind": None if not reasons else "mcp_installation_mismatch", "reasons": reasons,
            "runtime": runtime, "source_runtime": source, "manifest": comparison,
            "protocol": {"initialized": True, "server_info": server_info, "reported_identity": identity,
                         "tool_names": names, "tool_count": len(names), "inspection_only": not comparison.get("unexpected_tools"),
                         "checks": ["initialize", "tools/list"]},
            "authority": {"profile": "inspection", "repository_authorization_certified": False,
                          "live_client_configuration_changed": False, "tool_calls_executed": False}}


def probe(python: str, repo: str, *, timeout: int = 20) -> dict:
    """Compare an actual selected stdio server with the independent contract."""
    if (not isinstance(python, str) or not python or not Path(python).is_absolute()
            or any(ord(char) < 32 for char in python) or not Path(python).is_file() or not os.access(python, os.X_OK)):
        return _failure("invalid_mcp_executable", "Select an explicit absolute executable Python for the AMU MCP server.", exit_code=2)
    if (not isinstance(repo, str) or not repo or not Path(repo).is_absolute() or "\x00" in repo
            or not Path(repo).is_dir() or type(timeout) is not int or not 1 <= timeout <= 60):
        return _failure("invalid_mcp_probe", "Use an existing absolute diagnostic repository and a 1–60 second timeout.", exit_code=2)
    if importlib.util.find_spec("mcp") is None:
        return _failure("mcp_sdk_unavailable", "Optional MCP SDK is unavailable; base doctor checks remain usable. No dependency was installed.", status="unavailable")
    command = [sys.executable, "-I", "-B", "-m", "amu.mcp_verification", "--stdio-probe", "--server-python", python,
               "--repo", repo, "--timeout", str(timeout)]
    try:
        result = process.run(command, timeout=timeout + 5, stdout_limit=MAX_STDOUT_BYTES,
                             stderr_limit=MAX_STDERR_BYTES, env=mcp_access.child_environment())
        if result.error or result.returncode:
            return _failure("mcp_probe_" + (result.error or "startup_failed"),
                            "Bounded MCP diagnostic probe did not complete; no client configuration or repository source was changed.")
        data = _decode(result.stdout)
        if data.get("status") not in {"matched", "mismatch", "unavailable", "failed"} or not isinstance(data.get("manifest"), dict):
            raise ValueError("unexpected probe response")
        return data
    except (OSError, ValueError, UnicodeError, RecursionError):
        return _failure("mcp_probe_invalid_response", "MCP diagnostic response was unavailable, malformed or exceeded its limits.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio-probe", action="store_true", required=True)
    parser.add_argument("--server-python", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    arguments = parser.parse_args()
    try:
        result = asyncio.run(asyncio.wait_for(_wire(arguments.server_python, arguments.repo, arguments.timeout), arguments.timeout))
    except Exception:
        result = _failure("mcp_protocol_failure", "Selected AMU stdio server failed initialize/tools-list or its bounded deadline; verify the executable and optional SDK.")
    encoded = json.dumps(result, allow_nan=False, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_STDOUT_BYTES:
        encoded = json.dumps(_failure("mcp_probe_response_limit", "MCP diagnostic result exceeds its byte allowance."))
    print(encoded)


if __name__ == "__main__":
    main()
