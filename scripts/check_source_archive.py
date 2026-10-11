"""Check a gzip source archive without extracting or executing its contents.

This is a release completeness/safety check, not a trusted-source signature or
license decision. Limits include decompressed bytes, including tar metadata.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import zlib
from pathlib import Path

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 5_000
CHUNK_BYTES = 64 * 1024
REQUIRED_FILES = frozenset({
    "MANIFEST.in", "pyproject.toml", "README.md", "constraints.txt", "THIRD-PARTY.txt",
    "amu/cli.py", "amu/mcp_server.py", "amu/mcp_access.py", "amu/services/mapping.py",
    "amu/mcp_manifest.py", "amu/mcp_verification.py", "amu/mcp_transport.py", "amu/repository_diagnostics.py",
    "amu/services/diagnostics.py", "amu/services/impact.py", "amu/services/impact_report.py",
    "amu/benchmark_data/public-api-v1.json", "amu/benchmark_data/relations-v1.json",
    "amu/fixture_verification.py", "amu/patch.py", "amu/change_analysis.py", "amu/check_drafts.py",
    "amu/benchmark_data/shared-cache-v1/cache_case.py",
    "amu/benchmark_data/shared-cache-v1/protected_checks.py", "amu/benchmark_data/shared-cache-v1/runner.py",
    "amu/benchmark_data/shared-cache-v1/contract.json", "amu/benchmark_data/shared-cache-v1/manifest.json",
    "amu/benchmark_data/shared-cache-v1/patches/compatible-refactor.diff",
    "amu/benchmark_data/shared-cache-v1/patches/wrong-key.diff",
    "amu/benchmark_data/shared-cache-v1/patches/class-state.diff",
    "amu/benchmark_data/shared-cache-v1/patches/corrected.diff",
    "tests/test_fixture_verification.py", "tests/test_fixture_cli.py",
    "tests/test_concrete_patch.py", "tests/test_change_analysis.py", "tests/test_patch_adapters.py",
    "tests/test_check_drafts.py", "docs/phase3-check-drafts.md",
    "tests/test_graph_patch_prerequisite.py",
    "tests/__init__.py", "tests/conftest.py", "tests/fake_entire.py", "tests/bin/entire",
    "tests/fixtures/impact.json", "tests/fixtures/diff.json", "tests/fixtures/search.json",
    "tests/fixtures/symbols.ndjson", "tests/test_source_archive.py", "tests/test_mcp_stdio.py",
    "tests/test_mcp_security_stdio.py", "tests/test_model_disable_switch.py", "tests/test_runner_output_bounds.py",
    "tests/test_engine_pin.py",
    "tests/test_mcp_manifest.py", "tests/test_mcp_verification.py", "tests/test_repository_diagnostics.py",
    "tests/test_doctor_diagnostics.py", "tests/test_impact_service.py", "tests/test_impact_adapters.py",
    "tests/test_doc_queue.py", "tests/test_mcp_search_inspection.py",
    "tests/test_doc_review_read_boundary.py",
    "tests/test_mcp_wire_protocol.py",
    "tests/test_mcp_transport.py",
    "tests/test_graph_service_parity.py", "tests/test_documentation_service_parity.py",
    "scripts/check_constraints.py", "scripts/check_source_archive.py", "scripts/install.sh",
    "scripts/phase2_fixture_evidence.py",
    "scripts/smoke_installed.py", "scripts/smoke_mcp_installed.py", "scripts/smoke_pilot_installed.py",
    "scripts/smoke_mcp_workflow.py",
    "scripts/smoke_mcp_inspection.py",
    "scripts/smoke_mcp_wire.py",
    "scripts/smoke_mcp_patch.py",
    "docs/RELEASE-READINESS.md", "docs/STATE-RECOVERY.md", "docs/API-ACCURACY.md", "docs/MCP-SECURITY.md",
    "docs/IMPACT-ANALYSIS.md", "docs/IMPROVEMENT-STATUS.md", "docs/phase2-quickstart.md",
    "docs/evidence/graph-services-2026-10-09.json",
    "web/public/card.html", "web/public/graph.html", "web/public/card.js", "web/public/graph.js",
    "web/public/viewer-input.js", "web/public/viewers.css", "web/public/install.sh",
    "web/public/vendor/d3.min.js", "web/public/vendor/d3.LICENSE",
    "web/package.json", "web/package-lock.json", "web/playwright.config.js",
    "web/tests/accessibility.spec.js", "web/tests/hardening.spec.js", ".github/workflows/ci.yml",
})
_FORBIDDEN_PARTS = frozenset({
    ".git", ".amu", ".entire", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "_nobin", ".ssh", ".aws", ".codex",
})


class ArchiveError(ValueError):
    """The archive cannot be accepted as a source release candidate."""


class _BoundedReader:
    """Bound gzip expansion before tarfile reads even a metadata extension."""

    def __init__(self, stream):
        self.stream = stream
        self.received = 0

    def read(self, size=-1):
        remaining = MAX_UNPACKED_BYTES - self.received
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        data = self.stream.read(requested)
        self.received += len(data)
        if self.received > MAX_UNPACKED_BYTES:
            raise ArchiveError("archive exceeds decompressed byte limit")
        return data


def _parts(name, *, directory=False):
    if not isinstance(name, str) or len(name) > 4096:
        raise ArchiveError("unsafe archive path")
    if directory and name.endswith("/"):
        name = name[:-1]
    parts = name.split("/")
    if (not name or "\\" in name or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(part in {"", ".", ".."} or ":" in part for part in parts)):
        raise ArchiveError("unsafe archive path")
    return parts


def _check_constraints(content):
    pins = set()
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError:
        raise ArchiveError("archived constraints are not UTF-8") from None
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        matched = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)(?:\s*;.*)?", line)
        if not matched:
            raise ArchiveError(f"archived constraints are not exact pins at line {number}")
        name = re.sub(r"[-_.]+", "-", matched[1]).lower()
        if name in pins:
            raise ArchiveError("archived constraints contain duplicate pins")
        pins.add(name)
    if not pins:
        raise ArchiveError("archived constraints are empty")


def check_archive(path):
    """Return bounded archive inventory/digests, or raise ArchiveError.

    Members are read as bytes, never written to disk, imported, rendered or used
    as instructions. Only regular files and directories in one root are allowed.
    """
    path = Path(path)
    root = None
    names = set()
    files = {}
    members = 0
    content_bytes = 0
    constraints = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE_BYTES:
                raise ArchiveError("archive must be a regular file within the compressed byte limit")
            with gzip.GzipFile(fileobj=source, mode="rb") as expanded:
                bounded = _BoundedReader(expanded)
                with tarfile.open(fileobj=bounded, mode="r|") as archive:
                    for member in archive:
                        members += 1
                        if members > MAX_MEMBERS:
                            raise ArchiveError("archive exceeds member-count limit")
                        parts = _parts(member.name, directory=member.isdir())
                        if root is None:
                            root = parts[0]
                            if not root.startswith("amu-") or root == "amu-":
                                raise ArchiveError("archive needs one amu-version root directory")
                        if parts[0] != root:
                            raise ArchiveError("archive has multiple root directories")
                        name = "/".join(parts)
                        if name in names:
                            raise ArchiveError("archive has duplicate member paths")
                        names.add(name)
                        if not (member.isdir() or member.isreg()) or member.issparse():
                            raise ArchiveError("archive links, sparse files and special members are forbidden")
                        if member.size < 0 or member.size > MAX_MEMBER_BYTES or (member.isdir() and member.size):
                            raise ArchiveError("archive member exceeds size limit or has invalid directory size")
                        if len(parts) == 1:
                            if not member.isdir():
                                raise ArchiveError("archive root must be a directory")
                            continue
                        relative = "/".join(parts[1:])
                        if (any(part in _FORBIDDEN_PARTS or part == ".env" or part.startswith(".env.")
                                for part in parts[1:]) or relative.endswith((".pyc", ".pyo"))
                                or (parts[-1].startswith("release-candidate") and parts[-1].endswith(".json"))):
                            raise ArchiveError("archive contains generated/private state or candidate sidecar")
                        if member.isdir():
                            continue
                        digest = hashlib.sha256()
                        size = 0
                        capture = bytearray() if relative == "constraints.txt" else None
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise ArchiveError("archive file body is unavailable")
                        with stream:
                            while chunk := stream.read(CHUNK_BYTES):
                                size += len(chunk)
                                content_bytes += len(chunk)
                                if size > MAX_MEMBER_BYTES or content_bytes > MAX_UNPACKED_BYTES:
                                    raise ArchiveError("archive contents exceed byte limit")
                                digest.update(chunk)
                                if capture is not None:
                                    if size > 256 * 1024:
                                        raise ArchiveError("archived constraints exceed byte limit")
                                    capture.extend(chunk)
                        if size != member.size:
                            raise ArchiveError("archive file body is truncated")
                        if capture is not None:
                            constraints = bytes(capture)
                        files[relative] = {"size": size, "sha256": digest.hexdigest(), "mode": member.mode}
                    # Reject appended hidden archives/data rather than accepting
                    # only the first tar stream. Normal tar padding is zero.
                    while chunk := archive.fileobj.read(CHUNK_BYTES):
                        if any(chunk):
                            raise ArchiveError("archive has non-padding trailing data")
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ArchiveError("archive changed during inspection")
    except (OSError, EOFError, tarfile.TarError, zlib.error) as exc:
        raise ArchiveError(f"cannot safely read source archive ({type(exc).__name__})") from None
    missing = sorted(REQUIRED_FILES - files.keys())
    if missing:
        raise ArchiveError("source archive is missing required release support: " + ", ".join(missing))
    empty = sorted(name for name in REQUIRED_FILES if not files[name]["size"] and name != "tests/__init__.py")
    if empty:
        raise ArchiveError("source archive has empty required support: " + ", ".join(empty))
    if not files["tests/bin/entire"]["mode"] & 0o111:
        raise ArchiveError("archived fake graph entry point is not executable")
    _check_constraints(constraints)
    return {"archive": str(path), "root": root, "members": members, "files": len(files),
            "content_bytes": content_bytes, "constraints_sha256": files["constraints.txt"]["sha256"],
            "required_files": len(REQUIRED_FILES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        report = check_archive(args.archive)
    except ArchiveError as exc:
        parser.exit(1, f"Source archive check failed: {exc}\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
