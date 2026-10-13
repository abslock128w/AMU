"""Private, bounded JSON report snapshots. Fresh source identity is checked per read.

No pickle, target execution, persisted parsed objects or identity inferred from mtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import types
from dataclasses import asdict
from pathlib import Path

VERSION = "snapshot-report-v1"
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORTS = 16
MAX_RETAINED_BYTES = 64 * 1024 * 1024
MAX_AGE_SECONDS = 3600


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _directory():
    parent = (
        Path(os.environ["AMU_HOME"])
        if os.environ.get("AMU_HOME")
        else Path(tempfile.gettempdir()) / f"amu-reports-{os.getuid()}"
    )
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise OSError("unsafe report home")
    root = parent / "report-snapshots"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("unsafe report snapshot directory")
    return root


def fingerprint(root, request):
    from . import pytest_fixtures, value_flow
    from .patch import parse_diff
    from .services import impact, impact_report, mapping

    production, production_complete = impact._python_inventory(root, production=True)
    tests, tests_complete = impact._test_inventory(root)
    names = set(production) | set(tests)
    names.update(f.path for f in parse_diff(request.patch))
    names.update({"pyproject.toml", ".amu/config.json", ".amu/feature_map.json", ".entire/settings.json"})
    names.update(pytest_fixtures.CONFIG_FILES)
    if request.coverage_evidence is not None:
        from .observed_tests import CONFIG_FILES
        names.update(CONFIG_FILES)
        names.add(request.coverage_evidence)
    files = []
    for name in sorted(names):
        if impact._source_path_present_or_unsafe(root, name):
            payload, identity = impact._source(root, name)
            files.append((name, _digest(payload), identity))
        else:
            files.append((name, None))
    analyzer = [
        (str(p.relative_to(Path(__file__).parent)), _digest(p.read_bytes()))
        for p in sorted(Path(__file__).parent.rglob("*.py"))
        if "__pycache__" not in p.parts
    ]
    provider = []
    if request.include_graph:
        home = os.environ.get("HOME", "")
        managed = (
            Path(
                os.environ.get("ENTIRE_PLUGIN_DIR")
                or (
                    str(Path(os.environ["XDG_DATA_HOME"]) / "entire/plugins")
                    if os.environ.get("XDG_DATA_HOME")
                    else str(Path(home) / ".local/share/entire/plugins")
                )
            )
            / "bin"
        )
        for command in ["entire", "entire-graph"]:
            executable = shutil.which(
                command,
                path=(str(managed) + os.pathsep if command.endswith("graph") else "") + os.environ.get("PATH", ""),
            )
            if executable:
                path = Path(executable).resolve()
                if path.stat().st_size > 128 * 1024 * 1024:
                    raise OSError("provider binary identity limit")
                provider.append((command, str(path), _digest(path.read_bytes())))
            else:
                provider.append((command, None))
        provider.append(
            ("environment", {k: os.environ.get(k) for k in ["PATH", "HOME", "ENTIRE_PLUGIN_DIR", "XDG_DATA_HOME"]})
        )
        # Native git/source/config freshness, not a graph query.
        capture = mapping.worktree_fingerprint(str(root), "report snapshot revalidation")
        provider.append(("worktree", {k: v for k, v in capture.items() if k != "at"}))
    runtime = []
    for fn in [mapping.inspect, value_flow.analyze_repository, impact_report.build_patch_packet]:
        if hasattr(fn, "__code__"):

            def constant_identity(value):
                if isinstance(value, types.CodeType):
                    return code_identity(value)
                if isinstance(value, (tuple, frozenset)):
                    entries = [constant_identity(v) for v in value]
                    if isinstance(value, frozenset):
                        entries.sort(key=lambda v: json.dumps(v, sort_keys=True))
                    return [type(value).__name__, entries]
                return [type(value).__name__, repr(value)]

            def code_identity(code):
                return [code.co_code.hex(), list(code.co_names), [constant_identity(v) for v in code.co_consts]]

            runtime.append(_digest(json.dumps(code_identity(fn.__code__)).encode()))
    import importlib.metadata
    import sys

    dependencies = sorted(
        (d.metadata.get("Name", ""), d.version, str(d.locate_file(""))) for d in importlib.metadata.distributions()
    )
    envelope = {k: v for k, v in asdict(request).items() if k not in {"section", "cursor", "page_size"}}
    root_info = root.stat()
    binding = {
        "version": VERSION,
        "repository": (str(root), root_info.st_dev, root_info.st_ino),
        "head": impact.state.git_head(str(root)),
        "sources": files,
        "analyzer": analyzer,
        "python": (sys.version, sys.executable),
        "dependencies": dependencies,
        "inventory_complete": [production_complete, tests_complete],
        "runtime_limits": {
            module.__name__: {k: v for k, v in vars(module).items() if k.startswith("MAX_") and type(v) in {int, float}}
            for module in [impact, impact_report, value_flow, pytest_fixtures]
        },
        "provider": provider,
        "runtime_adapters": runtime,
        "pytest_environment": {name: _digest(os.environ.get(name, "").encode()) for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")},
        "request": envelope,
    }
    return _digest(json.dumps(binding, sort_keys=True, separators=(",", ":"), default=str).encode())


def load(key):
    try:
        path = _directory() / (key + ".json")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_REPORT_BYTES:
                return None
            if time.time() - info.st_mtime > MAX_AGE_SECONDS:
                return None
            data = json.loads(stream.read(MAX_REPORT_BYTES + 1))
        body = data["snapshot"]
        digest = _digest(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
        return body if data["sha256"] == digest and body["binding"] == key else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(key, *, identity, groups, limitations, max_bytes, max_rows):
    if key is None:
        return
    body = {
        "binding": key,
        "identity": identity,
        "groups": groups,
        "limitations": limitations,
        "max_bytes": max_bytes,
        "max_rows": max_rows,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    payload = json.dumps(
        {"sha256": _digest(canonical), "snapshot": body}, sort_keys=True, separators=(",", ":")
    ).encode()
    if len(payload) > MAX_REPORT_BYTES:
        return
    try:
        root = _directory()
        fd, name = tempfile.mkstemp(prefix=".report-", dir=root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(name, root / (key + ".json"))
        finally:
            if os.path.exists(name):
                os.unlink(name)
        files = sorted(
            (p for p in root.glob("*.json") if not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True
        )
        retained = 0
        for i, path in enumerate(files):
            retained += path.stat().st_size
            if (
                i >= MAX_REPORTS
                or retained > MAX_RETAINED_BYTES
                or time.time() - path.stat().st_mtime > MAX_AGE_SECONDS
            ):
                path.unlink()
    except OSError:
        # Caching is optional; unsafe/unavailable storage never substitutes evidence.
        return
