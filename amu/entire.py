"""All subprocess calls to the `entire` CLI live here. Nothing raises; failures come back as
`{"error": "..."}` dicts / `None` / error strings so every caller degrades instead of crashing."""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from amu import process

MAX_QUERY_BYTES = 4 * 1024 * 1024
MAX_SYMBOL_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_RECORD_BYTES = 1024 * 1024
MAX_SYMBOL_RECORDS = 100_000
MAX_SNAPSHOT_RECORDS = 1_000_000

INSTALL_HINT = (
    "brew tap entireio/tap && brew trust entireio/tap && brew install --cask entire && entire plugin install graph"
)


def _run(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    limit = MAX_SYMBOL_BYTES if args[:2] == ["graph", "symbols"] else MAX_QUERY_BYTES
    try:
        r = process.run(["entire", *args], timeout=timeout, stdout_limit=limit, stderr_limit=MAX_STDERR_BYTES)
    except (ValueError, OSError):
        return 126, "", "entire command arguments or output limits were invalid; no graph output was accepted"
    if r.error:
        return r.returncode, "", _process_error(r.error, args, timeout, limit)
    try:
        return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8", errors="replace")
    except UnicodeError:
        return 126, "", "entire returned invalid UTF-8; no partial graph output was accepted"


def _process_error(error: str, args: list[str], timeout: int, limit: int) -> str:
    operation = " ".join(args[:2])
    if error == "missing":
        return "entire CLI not found (not installed or not on PATH)"
    if error == "timeout":
        return f"entire {operation} timed out after {timeout}s; no partial output was accepted"
    if error == "output_limit":
        return (f"entire {operation} exceeded an output byte limit (stdout {limit}, stderr {MAX_STDERR_BYTES}); "
                "no partial output was accepted; narrow the repository/query")
    return f"entire {operation} failed while reading output; no partial output was accepted"


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON value")
    return value


def _invalid_constant(_value):
    raise ValueError("nonfinite JSON value")


def _parse_json(text):
    return json.loads(text, object_pairs_hook=_unique_pairs, parse_float=_finite_float, parse_constant=_invalid_constant)


def _json(args: list[str], timeout: int = 180) -> dict[str, Any]:
    rc, out, err = _run(args, timeout)
    if rc != 0 or not out.strip():
        failure = {"error": err.strip() or f"exit {rc}", "rc": rc}
        if rc in {124, 125, 126}:
            # These codes may also originate from the provider itself. The
            # controlled runner diagnostic describes transport failures; a
            # return code alone cannot establish their cause.
            failure.update(complete=False)
        return failure
    try:
        data = _parse_json(out)
    except (ValueError, RecursionError):
        return {"error": "non-JSON output", "raw": out[:500]}
    return data if isinstance(data, dict) else {"data": data}


def available() -> bool:
    return shutil.which("entire") is not None


def graph_available() -> bool:
    rc, out, _ = _run(["graph", "version"], 30)
    return rc == 0 and bool(out.strip())


def version() -> str:
    rc, out, _ = _run(["version"], 30)
    return out.splitlines()[0].replace("Entire CLI ", "").strip() if rc == 0 and out else "missing"


def graph_version() -> str:
    rc, out, _ = _run(["graph", "version"], 30)
    return out.strip() if rc == 0 else "missing"


def index(repo: str = ".") -> dict:
    return _json(["graph", "index", "--repo", repo])


class _SnapshotStream:
    """Hold at most one bounded record, not a snapshot-sized list of objects."""

    def __init__(self, stream):
        self.stream = stream
        self.pending = bytearray()
        self.lines = 0
        self.encoding = None
        self.error = None

    def _line(self, line: bytes):
        if len(line) > MAX_RECORD_BYTES:
            raise ValueError("graph snapshot record byte limit exceeded")
        if not line.strip():
            return
        if self.lines >= MAX_SNAPSHOT_RECORDS:
            raise ValueError("graph snapshot record count limit exceeded")
        try:
            record = _parse_json(line.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("graph snapshot contained invalid NDJSON") from exc
        if self.lines == 0:
            self.encoding = "compact" if isinstance(record, list) else "verbose"
            if self.encoding == "compact" and not (
                len(record) >= 3 and record[0] == "h" and type(record[1]) is int
                and record[1] >= 1 and isinstance(record[2], dict)
            ):
                raise ValueError("graph snapshot contained malformed compact records")
        if self.encoding == "compact":
            if not (isinstance(record, list) and len(record) >= 2 and isinstance(record[0], str)):
                raise ValueError("graph snapshot contained malformed compact records")
        elif not (isinstance(record, dict) and isinstance(record.get("record_type"), str) and record["record_type"]):
            raise ValueError("graph snapshot contained an untyped record")
        self.lines += 1

    def feed(self, chunk: bytes):
        try:
            pieces = (self.pending + chunk).split(b"\n")
            for line in pieces[:-1]:
                self._line(line)
            self.pending = bytearray(pieces[-1])
            if len(self.pending) > MAX_RECORD_BYTES:
                raise ValueError("graph snapshot record byte limit exceeded")
            self.stream.write(chunk)
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            raise

    def finish(self):
        if self.pending:
            self._line(bytes(self.pending))
        if not self.lines:
            raise ValueError("graph snapshot returned no records")


def snapshot(repo: str, out_path: str, worktree: bool = False) -> dict:
    """Persist a graph snapshot.

    Snapshotting ``HEAD`` remains available for callers that explicitly need a
    committed reference.  Edit workflows must request ``worktree=True`` so the
    saved evidence describes the files the agent just changed, not the last
    commit.
    """
    args = ["graph", "snapshot", "--repo", repo, "--format", "compact-ndjson"]
    if worktree:
        args.append("--worktree")
    # Preserve the previous durable snapshot until the complete new output has
    # been written.  ``os.replace`` is atomic on the local filesystem.
    destination = Path(out_path)
    temporary_path: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False) as temporary:
            temporary_path = temporary.name
            validator = _SnapshotStream(temporary)
            result = process.run(["entire", *args], timeout=300, stdout_limit=MAX_SNAPSHOT_BYTES,
                                 stderr_limit=MAX_STDERR_BYTES, stdout_sink=validator.feed, capture_stdout=False)
            if result.error or result.returncode:
                return {"error": validator.error or (_process_error(result.error, args, 300, MAX_SNAPSHOT_BYTES)
                        if result.error else result.stderr.decode("utf-8", errors="replace").strip() or f"exit {result.returncode}"),
                        "rc": result.returncode, "complete": False}
            validator.finish()
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    except (OSError, ValueError) as exc:
        return {"error": str(exc) if isinstance(exc, ValueError) else f"could not persist graph snapshot: {exc}"}
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    return {"lines": validator.lines, "path": str(destination)}


def capabilities(repo: str = ".") -> dict:
    """Read installation-wide graph capabilities.

    ``entire graph capabilities`` deliberately has no repository selector.  Keeping
    ``repo`` in this wrapper preserves the public API used by callers, but passing
    it through makes the real CLI reject the command and silently forces consumers
    onto their fallback coverage assumptions.
    """
    return _json(["graph", "capabilities", "--json"], 60)


def search(repo: str, query: str) -> dict:
    return _json(["graph", "search", "--repo", repo, "--profile", "full", "--query", query, "--format", "json"])


def definition(repo: str, symbol: str) -> str:
    rc, out, err = _run(["graph", "def", symbol, "--repo", repo], 60)
    return out if rc == 0 else f"Error running def: {err}"


def impact_json(
    repo: str, symbol: str, depth: int = 2, limit: int = 10_000, file: str | None = None, head: bool = False
) -> dict:
    """Read a full-profile impact from the live worktree by default.

    ``file`` disambiguates a name defined in several files.  The high bounded
    default avoids silently comparing a provider's tiny display page as if it
    were a full relationship set; callers still reject a response whose
    reported total exceeds this limit. ``head=True`` is
    opt-in for an explicitly validated committed-cache query; using it by
    default can silently return a stale or structurally incomplete graph.
    """
    args = [
        "graph",
        "impact",
        "--repo",
        repo,
        "--symbol",
        symbol,
        "--depth",
        str(depth),
        "--limit",
        str(limit),
        "--profile",
        "full",
        "--format",
        "json",
    ]
    if file:
        args += ["--file", file]
    if head:
        args.append("--head")
    return _json(args)


def impact_text(repo: str, symbol: str, depth: int = 2) -> str:
    rc, out, err = _run(
        ["graph", "impact", "--symbol", symbol, "--depth", str(depth), "--repo", repo, "--profile", "full"]
    )
    if rc == 127:
        return "Error running impact: entire CLI not found (not installed or not on PATH)"
    if rc != 0:
        return f"Error running impact: {err}"
    return out


def neighbors_json(
    repo: str, symbol: str, relation: str = "CALLS", direction: str = "in", internal_only: bool = False
) -> dict:
    args = [
        "graph",
        "neighbors",
        "--repo",
        repo,
        "--symbol",
        symbol,
        "--relation",
        relation,
        "--direction",
        direction,
        "--format",
        "json",
    ]
    if internal_only:
        args.append("--internal-only")
    return _json(args)


def diff_json(repo: str, base: str, head: str = "HEAD") -> dict | None:
    d = _json(["graph", "diff", "--repo", repo, "--base", base, "--head", head, "--json"])
    return None if "error" in d else d


def diff_text(repo: str, base: str, head: str = "HEAD") -> str:
    rc, out, err = _run(["graph", "diff", "--repo", repo, "--base", base, "--head", head])
    return out if rc == 0 else f"Error running diff: {err}"


def symbols_result(repo: str, worktree: bool = True) -> tuple[list[dict], str | None]:
    """Read symbol records without conflating an empty graph with a failed query.

    An empty successful result is valid for a file with no supported public
    symbols.  A command or NDJSON failure is not equivalent evidence and must
    remain visible to callers that make approval decisions from this list.
    """
    args = ["graph", "symbols", "--repo", repo, "--format", "ndjson"]
    if worktree:
        args.append("--worktree")
    rc, out, err = _run(args, 300)
    if rc != 0:
        return [], err.strip() or f"exit {rc}"
    rows: list[dict] = []
    malformed = False
    for line in io.StringIO(out):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES or len(rows) >= MAX_SYMBOL_RECORDS:
            return [], "entire graph symbols exceeded a record byte/count limit; narrow the repository/query"
        try:
            row = _parse_json(line)
        except (ValueError, RecursionError):
            malformed = True
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            malformed = True
    if malformed:
        return [], "non-NDJSON output from entire graph symbols"
    return rows, None


def symbols(repo: str, worktree: bool = True) -> list[dict]:
    """Compatibility helper for non-verdict lookups that can tolerate no rows."""
    rows, _ = symbols_result(repo, worktree)
    return rows


def verify(repo: str, test_cmd: str) -> dict:
    rc, out, err = _run(["graph", "verify", "--repo", repo, "--test", test_cmd], 600)
    return {"rc": rc, "verdict": out.strip(), "stderr": err.strip()[-500:]}


def doctor() -> dict:
    return _json(["graph", "doctor", "--json"], 60)


def checkpoint_list() -> list[dict]:
    d = _json(["checkpoint", "list", "--json"], 60)
    if "error" in d:
        return []
    data = d.get("data", d)
    return data if isinstance(data, list) else data.get("checkpoints", [])


def graph_checkpoint(checkpoint_id: str, repo: str = ".") -> str:
    rc, out, err = _run(["graph", "checkpoint", checkpoint_id, "--repo", repo])
    return out if rc == 0 else f"Error running checkpoint: {err}"


def status_text() -> str:
    rc, out, err = _run(["status"], 30)
    return out.strip() if rc == 0 else f"entire status unavailable: {err.strip()}"
