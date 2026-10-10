"""Regression coverage for the PRD's core offline CLI lifecycle.

The recorded ``entire`` fixture normally models a deliberately out-of-scope
diff.  This test keeps its map/symbol evidence but substitutes one declared,
in-scope signature change, so every phase can demonstrate the successful
workflow rather than treating a guardrail failure as success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _run_amu(repo: Path, *args: str) -> dict:
    """Run one JSON CLI command and fail with its output if its contract breaks."""
    # The fixture intentionally contains an ``amu/`` directory to match the
    # recorded graph evidence.  Keep the checkout package first so Python
    # does not mistake that fixture directory for the application package.
    checkout = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [checkout, os.environ.get("PYTHONPATH", "")]))}
    result = subprocess.run(
        [sys.executable, "-m", "amu.cli", *args, "--json", "--repo", str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"amu {' '.join(args)} failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    return json.loads(result.stdout)


def _write_repo(repo: Path) -> Path:
    """Create a committed mini project whose test and doc paths match fixture evidence."""
    repo.mkdir()
    (repo / "amu").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / "amu" / "classify.py").write_text("def classify_consumers(value):\n    return value\n")
    (repo / "tests" / "test_amu.py").write_text("def test_lifecycle_fixture():\n    assert True\n")
    (repo / "docs" / "guide.md").write_text(
        "# Guide\n\n## `amu/classify.py#classify_consumers`\nThe initial documented shape.\n"
    )
    (repo / "AGENTS.md").write_text("# Local agent notes\n")
    (repo / "CLAUDE.md").write_text("# Local project notes\n")
    (repo / ".gitignore").write_text(".amu/\n__pycache__/\n.pytest_cache/\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'lifecycle-fixture'\nversion = '0.0.0'\n")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Amu Test", "-c", "user.email=amu@example.test", "commit", "-qm", "baseline"],
        cwd=repo,
        check=True,
    )
    return repo


def _clean_graph_fixture(source: Path, destination: Path) -> Path:
    """Reuse recorded map evidence with a diff that is valid for this contract."""
    destination.mkdir()
    impact = json.loads((source / "impact.json").read_text())
    # This success-path fixture models a fully parsed mini project. Separate
    # refresh/readiness tests retain the provider's partial-analysis cases.
    impact["partial_failures"] = []
    # The recorded card fixture intentionally samples its 30 caller rows.
    # The lifecycle fixture exercises an approvable baseline, so make its
    # copied provider response complete rather than hiding the truncation.
    for section in ("callers", "callees", "type_consumers", "data_flows"):
        group = impact.get(section)
        if isinstance(group, dict) and isinstance(group.get("entries"), list):
            group["total"] = len(group["entries"])
    (destination / "impact.json").write_text(json.dumps(impact))
    shutil.copy2(source / "symbols.ndjson", destination / "symbols.ndjson")
    (destination / "diff.json").write_text(
        json.dumps(
            {
                "base": "baseline",
                "head": "HEAD",
                "files": [
                    {
                        "path": "amu/classify.py",
                        "status": "M",
                        "language": "Python",
                        "changes": [
                            {
                                "type": "signature_changed",
                                "kind": "function",
                                "name": "classify_consumers",
                                "old_signature": "def classify_consumers(value)",
                                "new_signature": "def classify_consumers(value, depth=2)",
                                "dependents_count": 1,
                            }
                        ],
                    }
                ],
            }
        )
    )
    return destination


def test_prd_cli_lifecycle_persists_validated_state_and_report(fake_entire, monkeypatch, tmp_path):
    """init → map → plan → all phase checks → docs draft → done remains machine-readable."""
    repo = _write_repo(tmp_path / "repo")
    fixtures = _clean_graph_fixture(fake_entire, tmp_path / "clean-graph-fixtures")
    monkeypatch.setenv("AMU_FAKE_FIXTURES", str(fixtures))

    initialized = _run_amu(repo, "init")
    assert initialized["snapshot_lines"] == 1
    assert initialized["files"] == 3
    assert initialized["docs_graph"]["sections"] == 2
    assert (repo / ".amu" / "snapshot.ndjson").exists()
    assert (repo / ".amu" / "docs-graph.json").exists()
    # The test's interpreter is guaranteed to have pytest; PATH intentionally
    # hides package-manager bins in conftest, so do not depend on a bare binary.
    config_path = repo / ".amu" / "config.json"
    config = json.loads(config_path.read_text())
    config["test_cmd"] = f"{sys.executable} -m pytest -q"
    config["build_cmd"] = f"{sys.executable} -m compileall -q amu"
    config_path.write_text(json.dumps(config))

    mapped = _run_amu(repo, "map", "--symbol", "amu/classify.py#classify_consumers")
    assert mapped["root"]["file"] == "amu/classify.py"
    assert mapped["resolution"]["sound"] > 0
    assert all(node["reason"] and node["verify"] for node in mapped["nodes"] if node["confidence"] != "sound")

    planned = _run_amu(repo, "plan", "--docs-file", "docs/guide.md", "--approve")
    contract_path = Path(planned["contract"])
    contract = json.loads(contract_path.read_text())
    assert [phase["kind"] for phase in planned["phases"]] == ["additive", "leaves", "interior", "removal"]
    assert contract["map_hash"] == mapped["map_hash"]
    assert contract["base_sha"] != "no-git"
    assert contract["targets"] == planned["targets"]
    assert contract["targets"] == [{"symbol": "amu/classify.py#classify_consumers", "change_class": "signature"}]

    assert planned["docs_files"] == ["docs/guide.md"]
    assert "docs/guide.md" in contract["allowed_files"]
    assert "docs/guide.md" in contract["phases"][-1]["files"]

    for phase in range(1, 5):
        if phase == 3:
            (repo / "amu" / "classify.py").write_text(
                "def classify_consumers(value, depth=2):\n    return value\n"
            )
            refreshed = _run_amu(
                repo, "refresh", "--symbol", "amu/classify.py#classify_consumers", "--change", "signature"
            )
            assert refreshed["complete"] is True
        checked = _run_amu(repo, "check", "--phase", str(phase))
        assert checked["phase"] == phase
        assert checked["passed"] is True
        assert checked["violations"] == []
        assert checked["tests"]["passed"] is True
        assert checked["build"]["passed"] is True
        assert checked["checker"]["role"] == "checker"

    state = json.loads((repo / ".amu" / "state.json").read_text())
    assert len(state["history"]) == 4
    assert {state["nodes"][node["id"]] for node in mapped["nodes"]} == {"green"}

    doc_before = (repo / "docs" / "guide.md").read_text()
    documented = _run_amu(repo, "docs", "--mode", "draft")
    assert documented["mode"] == "draft"
    assert documented["applied"] == []
    assert documented["candidates"][0]["candidate"] == "amu/classify.py#classify_consumers"
    assert documented["candidates"][0]["consistent_with_diff"] is True
    assert (repo / "docs" / "guide.md").read_text() == doc_before

    candidate_id = documented["candidates"][0]["id"]
    prepared = _run_amu(repo, "docs", "--prepare", candidate_id)
    Path(prepared["prepared"]["file"]).write_text(
        "Use classify_consumers(value, depth=2) to select the traversal depth.\n"
    )
    applied = _run_amu(repo, "docs", "--apply", candidate_id, "--review")
    assert applied["applied"] == [candidate_id]
    assert "depth=2" in (repo / "docs" / "guide.md").read_text()
    assert applied["docs_graph"]["graph_hash"] != documented["docs_graph"]["graph_hash"]
    # Refresh currently binds the whole worktree, including documentation.
    # Capture the accepted docs edit before closing the contract.
    _run_amu(repo, "refresh", "--symbol", "amu/classify.py#classify_consumers", "--change", "signature")
    # Refresh proves graph freshness, not tests. Accepted docs edits also
    # change the checked worktree, so renew full check evidence before done.
    _run_amu(repo, "check")

    report = _run_amu(repo, "done")
    saved_report = json.loads((repo / ".amu" / "report.json").read_text())
    sync_state = json.loads((repo / ".amu" / "sync-state.json").read_text())
    assert report == saved_report
    assert report["declared"]["targets"] == contract["targets"]
    assert report["actual"]["node_states"] == state["nodes"]
    assert report["findings"] == []
    assert report["snapshot_lines"] == 1
    assert report["docs"]["graph"]["sections"] == 2
    assert "relationship_comparisons" in report["docs"]
    assert sync_state["snapshot_lines"] == 1
    assert (repo / ".amu" / "snapshot.ndjson").read_text().startswith('{"record_type":"file"')
