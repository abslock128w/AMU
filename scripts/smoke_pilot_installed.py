"""Optional operator drill for an installed wheel and two explicitly chosen binaries.

Run with the candidate environment's Python, never an editable installation::

    /candidate/bin/python -I scripts/smoke_pilot_installed.py \
        --entire /absolute/path/to/entire \
        --graph-plugin /absolute/path/to/entire-graph

All repository, HOME, plugin-exposure and cache writes belong to one disposable
directory. No model requests, installs, pushes or original-plugin edits occur.
The JSON report preserves successful steps and exact failures; a partial drill
exits nonzero. This tiny trusted fixture is not a population-accuracy benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

TARGET = "pilotpkg/api.py#target"
INITIAL_SOURCE = (
    "def target(value):\n    return value\n\n\n"
    "def same_file_caller(value):\n    return target(value)\n\n\n"
    "def isolated():\n    return 17\n"
)
EDITED_SOURCE = INITIAL_SOURCE.replace(
    "def target(value):\n    return value", "def target(value, *, offset=0):\n    return value + offset", 1,
)


class DrillFailure(RuntimeError):
    pass


def _binary(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise DrillFailure("binary arguments must be explicit absolute paths")
    try:
        path = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise DrillFailure(f"binary cannot be resolved: {raw}: {exc}") from exc
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DrillFailure(f"binary is not an executable regular file: {path}")
    return path


def _identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


class Drill:
    def __init__(self, root: Path, entire: Path, plugin: Path, timeout: int, total_timeout: int):
        import amu
        from amu import process

        prefix = Path(sys.prefix).resolve()
        package = Path(amu.__file__).resolve()
        if not package.is_relative_to(prefix) or not Path(process.__file__).resolve().is_relative_to(prefix):
            raise DrillFailure(f"AMU must be a non-editable installed wheel under {prefix}; imported {package}")
        if amu.__version__ != importlib.metadata.version("amu"):
            raise DrillFailure("installed distribution and imported AMU versions differ")
        root = root.resolve()
        self.process = process
        self.root = root
        self.repo = root / "repo"
        self.repo.mkdir()
        self.timeout = timeout
        self.deadline = time.monotonic() + total_timeout
        home = root / "home"
        home.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        git = shutil.which("git")
        if git is None:
            raise DrillFailure("Git is required; no tools will be installed by this drill")
        self.git = str(Path(git).resolve())
        (bindir / "git").symlink_to(self.git)
        (bindir / "entire").symlink_to(entire)
        (bindir / "entire-graph").symlink_to(plugin)
        managed = home / ".local/share/entire/plugins/bin"
        managed.mkdir(parents=True)
        (managed / "graph").symlink_to(plugin)
        self.env = {
            "PATH": os.pathsep.join([str(bindir), str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
            "HOME": str(home),
            "AMU_HOME": str(home / ".amu"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "TMPDIR": str(root),
            "NO_COLOR": "1", "TERM": "dumb", "LANG": "en_US.UTF-8",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        self.report = {
            "schema_version": 1,
            "purpose": "supervised installed-wheel pilot operator drill",
            "complete": False,
            "application": {"version": amu.__version__, "package": str(package), "prefix": str(prefix)},
            "engine": {"dispatcher": _identity(entire), "graph_plugin": _identity(plugin)},
            "isolation": {"temporary_home": True, "credentials_inherited": False, "outbound_enabled": False,
                          "original_installation_modified": False, "fixture_only": True},
            "accuracy_claim": "selected tiny-fixture workflows only; no repository-population or runtime certification",
            "sequence_note": "The first done precedes reviewed documentation. A separate post-save check/done is required for combined completion.",
            "flows": {"lifecycle": False, "post_documentation_completion": False, "documentation_recovery": False},
            "steps": [], "failures": [],
        }

    def run(self, step: str, argv: list[str], *, expected: int = 0, json_output: bool = False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DrillFailure(f"{step}: total drill deadline exceeded")
        started = time.monotonic()
        result = self.process.run(
            argv, cwd=self.repo, env=self.env, timeout=min(self.timeout, remaining),
            stdout_limit=4 * 1024 * 1024, stderr_limit=64 * 1024,
        )
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        row = {"step": step, "command": argv, "returncode": result.returncode,
               "duration_seconds": round(time.monotonic() - started, 3),
               "status": "passed" if result.error is None and result.returncode == expected else "failed"}
        self.report["steps"].append(row)
        if result.error or result.returncode != expected:
            row.update(error_kind=result.error, stdout=stdout[:12000], stderr=stderr[-4000:])
            raise DrillFailure(f"{step}: expected exit {expected}, got {result.returncode}; {result.error or stderr or stdout}")
        if not json_output:
            row["summary"] = stdout[-1000:]
            return stdout
        try:
            payload = json.loads(stdout)
        except (ValueError, RecursionError) as exc:
            row.update(status="failed", stdout=stdout[:2000])
            raise DrillFailure(f"{step}: CLI output was not JSON") from exc
        if not isinstance(payload, dict):
            row["status"] = "failed"
            raise DrillFailure(f"{step}: expected a JSON object")
        row["summary"] = {key: payload[key] for key in (
            "complete", "passed", "approval_refused", "error", "next", "applied", "not_applied", "save_id",
        ) if key in payload}
        return payload

    def cli(self, step: str, *args: str, expected: int = 0) -> dict:
        return self.run(step, [sys.executable, "-I", "-m", "amu.cli", *args,
                               "--repo", str(self.repo), "--json"], expected=expected, json_output=True)

    def require(self, step: str, condition: bool, detail: str) -> None:
        row = {"step": step, "status": "passed" if condition else "failed", "detail": detail}
        self.report["steps"].append(row)
        if not condition:
            raise DrillFailure(f"{step}: {detail}")

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def setup(self) -> None:
        self.report["engine"]["dispatcher"]["version"] = self.run("dispatcher-version", ["entire", "version"])
        self.report["engine"]["graph_plugin"]["version"] = self.run("graph-version", ["entire", "graph", "version"])
        self.require("real-engine", "fake" not in self.report["engine"]["graph_plugin"]["version"].lower(),
                     "The explicitly supplied graph executable must not identify itself as a recorded fake.")
        self.write("pilotpkg/__init__.py", "")
        self.write("pilotpkg/api.py", INITIAL_SOURCE)
        self.write("pilotpkg/caller.py", "from .api import target\n\n\ndef cross_file_caller(value):\n    return target(value)\n")
        self.write("tests/test_api.py", "from pilotpkg.api import target, same_file_caller\n"
                   "from pilotpkg.caller import cross_file_caller\n\n\ndef test_required_behavior():\n"
                   "    assert target(4) == 4\n    assert same_file_caller(4) == 4\n    assert cross_file_caller(4) == 4\n")
        self.write("docs/guide.md", "# Guide\n\n## Public API\n\nUse `pilotpkg/api.py#target` with one value.\n\n"
                   "## Other\n\nKeep this unrelated section unchanged.\n")
        self.write(".gitignore", ".amu/\n__pycache__/\n.pytest_cache/\n")
        self.write("pyproject.toml", "[project]\nname = 'amu-pilot-fixture'\nversion = '0.0.0'\n")
        self.run("git-init", [self.git, "init", "-q"])
        for key, value in (("user.name", "AMU pilot drill"), ("user.email", "pilot@example.invalid"),
                           ("core.hooksPath", "/dev/null"), ("commit.gpgsign", "false")):
            self.run(f"git-config-{key}", [self.git, "config", "--local", key, value])
        self.run("git-add-fixture", [self.git, "add", "--", "pilotpkg", "tests", "docs", ".gitignore", "pyproject.toml"])
        self.run("git-baseline", [self.git, "commit", "-qm", "trusted disposable pilot baseline"])
        self.write(".amu/config.json", json.dumps({"outbound": {"enabled": False}}))
        self.cli("initialize", "init")
        config_path = self.repo / ".amu/config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(test_cmd=shlex.join([sys.executable, "-m", "pytest", "-q"]),
                      build_cmd=shlex.join([sys.executable, "-m", "compileall", "-q", "pilotpkg"]),
                      docs_dir="docs", outbound={"enabled": False})
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.write(".amu/doc-map.json", json.dumps({TARGET: [{"file": "docs/guide.md", "section": "Public API"}]}))
        self.require("outbound-blocked", config["outbound"]["enabled"] is False,
                     "Repository model requests are disabled, and the subprocess environment inherits no credentials/endpoints.")

    def lifecycle(self) -> None:
        observed = self.cli("isolated-function-inspection", "graph", "pilotpkg/api.py#isolated", "--relation", "CALLS")
        self.report["selected_fixture_observations"] = {
            "isolated_graph_warnings": observed.get("warnings", []),
            "isolated_graph_resolution": observed.get("resolution"),
            "isolated_observed_nodes": len(observed["nodes"]) if isinstance(observed.get("nodes"), list) else None,
            "isolated_observed_edges": len(observed["edges"]) if isinstance(observed.get("edges"), list) else None,
            "note": "The fixture has no source call sites for isolated(); other graph relation categories may exist.",
        }
        mapped = self.cli("pre-edit-map", "map", "--symbol", TARGET, "--change", "signature")
        evidence = mapped.get("graph_evidence") or {}
        self.report["selected_fixture_observations"].update(
            map_resolution=mapped.get("resolution"), map_approvable=evidence.get("approvable"),
            map_readiness_reasons=evidence.get("reasons", []),
        )
        baseline = (evidence.get("edge_baselines") or {}).get(TARGET, [])
        callers = {edge.get("related") for edge in baseline if edge.get("relation") == "CALLS"
                   and edge.get("direction") == "in" and edge.get("depth") == 1}
        self.report["selected_fixture_observations"]["direct_callers"] = sorted(callers)
        self.require("selected-same-and-cross-file-calls", {
            "pilotpkg/api.py#same_file_caller", "pilotpkg/caller.py#cross_file_caller",
        } <= callers, "Both deliberately constructed direct caller relationships appear in the exact target baseline.")
        planned = self.cli("approve-plan", "plan", "--docs-file", "docs/guide.md", "--approve")
        self.require("contract-recorded", bool(planned.get("contract")) and not planned.get("approval_refused"),
                     "A real pre-edit map and explicit --docs-file produced an approved contract; no contract fields were manually expanded.")
        self.require("documentation-approved-before-edits", planned.get("docs_files") == ["docs/guide.md"],
                     "The future documentation edit was explicitly visible in the approved pre-edit plan.")
        self.write("pilotpkg/api.py", EDITED_SOURCE)
        refreshed = self.cli("post-edit-refresh", "refresh", "--symbol", TARGET, "--change", "signature")
        self.require("refresh-complete", refreshed.get("complete") is True,
                     "The exact approved target has a current successful graph refresh.")
        for phase in planned["phases"]:
            checked = self.cli(f"check-phase-{phase['n']}", "check", "--phase", str(phase["n"]))
            tests = checked.get("tests") or {}
            self.require(f"phase-{phase['n']}-test-proof", checked.get("passed") is True
                         and tests.get("passed") is True
                         and (tests.get("evidence") or {}).get("passing_tests_verified") is True,
                         "Phase proof includes an actual passing pytest test, not skipped/collect-only evidence.")
        closed = self.cli("complete-lifecycle", "done")
        self.require("completion-positive", closed.get("complete") is True
                     and (closed.get("lifecycle") or {}).get("passed") is True,
                     "Completion is explicitly positive; exit 0 alone is not interpreted as a complete workflow.")
        self.report["flows"]["lifecycle"] = True

    def documentation(self) -> None:
        # Also exercise documentation after a lifecycle refusal. This is an
        # independent explicitly reviewed docs drill, not an expanded contract.
        self.write("pilotpkg/api.py", EDITED_SOURCE)
        original = (self.repo / "docs/guide.md").read_text(encoding="utf-8")
        listed = self.cli("list-documentation-candidates", "docs", "--mode", "draft")
        candidate = next((row for row in listed.get("candidates", [])
                          if row.get("candidate") == TARGET and row.get("doc_file") == "docs/guide.md"), None)
        self.require("mapped-documentation-candidate", candidate is not None,
                     "The worktree API change produces a mapped manual-review candidate.")
        candidate_id = candidate["id"]
        prepared = self.cli("prepare-documentation", "docs", "--prepare", candidate_id)
        draft_info = prepared.get("prepared") or {}
        draft = Path(draft_info.get("file", "")).resolve()
        self.require("draft-inside-fixture", draft.is_relative_to(self.repo) and draft.is_file(),
                     "The editable draft belongs to this disposable repository.")
        self.require("preparation-is-read-only", (self.repo / "docs/guide.md").read_text(encoding="utf-8") == original,
                     "Preparing a suggestion does not modify source documentation.")
        draft.write_text("Use target(value, offset=0); offset is an optional keyword-only addition.\n"
                         "Reviewed source: `pilotpkg/api.py#target`.\n", encoding="utf-8")
        if draft_info.get("review_id"):
            self.cli("preview-reviewed-draft", "docs-reviews", "--preview", draft_info["review_id"])
        applied = self.cli("save-reviewed-documentation", "docs", "--apply", candidate_id, "--review")
        self.require("documentation-save-positive", candidate_id in applied.get("applied", []),
                     "Explicitly reviewed prose was saved; no automated suggestion was accepted silently.")
        before_graph = (listed.get("docs_graph") or {}).get("graph_hash")
        after_graph = (applied.get("docs_graph") or {}).get("graph_hash")
        self.require("documentation-graph-updated", bool(before_graph) and bool(after_graph)
                     and before_graph != after_graph
                     and any(save.get("graph_refreshed") is True for save in applied.get("saves", [])),
                     "The reviewed save refreshed the documentation graph and changed its recorded identity.")
        self.post_documentation_completion()
        docpath = self.repo / "docs/guide.md"
        saved_text = docpath.read_text(encoding="utf-8")
        self.require("selected-section-only", "offset=0" in saved_text
                     and "## Other\n\nKeep this unrelated section unchanged.\n" in saved_text,
                     "The chosen section changed and the unrelated section survived.")
        history = self.cli("inspect-save-journal", "docs-saves")
        record = next((row for row in history.get("saves", []) if row.get("status") == "matches_saved"), None)
        self.require("recoverable-save", record is not None, "An inspectable save journal matches the current document.")
        undo = self.cli("guarded-documentation-undo", "docs-saves", "--undo", record["save_id"])
        self.require("undo-restores-original", undo.get("saved") is True
                     and docpath.read_text(encoding="utf-8") == original,
                     "Guarded undo restores the original document and creates its own recovery record.")
        later_edit = original + "\nA simulated later operator edit must not be overwritten.\n"
        docpath.write_text(later_edit, encoding="utf-8")
        refused = self.cli("changed-document-undo-refused", "docs-saves", "--undo", undo["save_id"], expected=2)
        self.require("later-edit-preserved", "changed" in str(refused.get("error", "")).lower()
                     and docpath.read_text(encoding="utf-8") == later_edit,
                     "Undo refuses an edited document and preserves the later operator edit.")
        self.report["flows"]["documentation_recovery"] = True

    def post_documentation_completion(self) -> None:
        """Never borrow pre-doc green proof or expand the approved file scope."""
        try:
            stale = self.cli("prior-green-cannot-complete-reviewed-doc-edit", "done", expected=1)
            self.require("documentation-invalidates-prior-check", stale.get("next") == "amu check"
                         and "successful current check evidence" in str(stale.get("error", "")),
                         "Accepted documentation cannot reuse the successful pre-documentation check evidence.")
            checked = self.cli("post-documentation-full-check", "check")
            self.require("reviewed-documentation-current-check", checked.get("passed") is True,
                         "The reviewed code-plus-documentation worktree passes an unqualified current check.")
            closed = self.cli("post-documentation-completion", "done")
            self.require("reviewed-documentation-current-completion", closed.get("complete") is True,
                         "Combined completion is positive after checking the reviewed documentation worktree.")
            self.report["flows"]["post_documentation_completion"] = True
        except (DrillFailure, OSError, ValueError, KeyError, TypeError) as exc:
            self.report["failures"].append({"flow": "post_documentation_completion", "error": str(exc)[:12000],
                                            "note": "Documentation was explicitly scoped before edits; the contract was not changed manually."})
            # Keep the recovery branch independent. Its guarded undo remains
            # useful even when the generated scope cannot approve this doc file.

    def exercise(self) -> dict:
        try:
            self.setup()
        except (DrillFailure, OSError, ValueError) as exc:
            self.report["failures"].append({"flow": "setup", "error": str(exc)[:12000]})
            return self.report
        for name, action in (("lifecycle", self.lifecycle), ("documentation_recovery", self.documentation)):
            try:
                action()
            except (DrillFailure, OSError, ValueError, KeyError, TypeError) as exc:
                self.report["failures"].append({"flow": name, "error": str(exc)[:12000]})
        self.report["complete"] = all(self.report["flows"].values()) and not self.report["failures"]
        self.report["temporary_fixture_removed_after_run"] = True
        return self.report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entire", required=True, help="absolute Entire dispatcher executable")
    parser.add_argument("--graph-plugin", required=True, help="absolute graph plugin executable; hashed and exposed only in temporary HOME")
    parser.add_argument("--timeout", type=int, default=60, help="per-command deadline, 1–60 seconds")
    parser.add_argument("--total-timeout", type=int, default=300, help="total deadline, 1–600 seconds")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60 or not 1 <= args.total_timeout <= 600:
        parser.error("timeouts must be within their documented bounds")
    try:
        entire, plugin = _binary(args.entire), _binary(args.graph_plugin)
        with tempfile.TemporaryDirectory(prefix="amu-installed-pilot-") as directory:
            report = Drill(Path(directory), entire, plugin, args.timeout, args.total_timeout).exercise()
        report["temporary_fixture_removed_after_run"] = True
    except (DrillFailure, OSError, ValueError) as exc:
        report = {"schema_version": 1, "complete": False, "failures": [{"flow": "preflight", "error": str(exc)}]}
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
