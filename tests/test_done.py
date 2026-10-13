import json
import subprocess
import sys

from typer.testing import CliRunner

from amu import cli


def _amu(args, cwd):
    return subprocess.run([sys.executable, "-m", "amu.cli", *args], cwd=cwd, capture_output=True, text=True)


def _approve_report_only_lifecycle(repo):
    """Give done a current approved lifecycle while a test isolates S4 reporting."""
    for _ in range(2):
        subprocess.run(
            ["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.invalid",
             "commit", "--allow-empty", "-qm", "fixture baseline"],
            cwd=repo, check=True,
        )
    amu_dir = repo / ".amu"
    amu_dir.mkdir(exist_ok=True)
    (amu_dir / "map.json").write_text(json.dumps({"map_hash": "report-only-map", "depth": 2, "nodes": []}))
    (amu_dir / "contract.json").write_text(
        json.dumps(
            {
                "targets": [],
                "base_sha": "HEAD~1",
                "map_hash": "report-only-map",
                "phases": [],
                "frozen_signatures": [],
                "allowed_files": [],
                "created_at": "fixture",
            }
        )
    )


def test_sweep_reports_findings_outside_contract_with_verify(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / ".amu").mkdir()
    (tmp_path / ".amu" / "contract.json").write_text(json.dumps({"base_sha": "HEAD~1", "allowed_files": ["amu/classify.py"], "phases": [], "targets": [], "frozen_signatures": [], "map_hash": "h", "created_at": "n"}))
    r = _amu(["sweep", "--json"], tmp_path)
    out = json.loads(r.stdout)
    assert r.returncode == 0
    assert any(f["path"] == "amu/graph.py" and f["verify"].startswith("amu map") for f in out["findings"])
    assert "findings outside the contract" in out["summary"] and "nothing found" not in out["summary"]


def test_sweep_never_exits_1_and_reports_depth(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    r = _amu(["sweep", "--json"], tmp_path)
    assert r.returncode == 0 and "at depth" in json.loads(r.stdout)["summary"]


def test_sweep_preserves_unknown_confidence_and_flags_an_undeclared_export_in_an_allowed_file(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / ".amu").mkdir()
    (tmp_path / ".amu" / "contract.json").write_text(
        json.dumps(
            {
                "base_sha": "HEAD~1",
                "allowed_files": ["amu/classify.py"],
                "phases": [],
                "targets": [],
                "frozen_signatures": [],
                "map_hash": "h",
                "created_at": "n",
            }
        )
    )
    (tmp_path / ".amu" / "map.json").write_text(
        json.dumps({"depth": 2, "nodes": [{"id": "n1", "path": "amu/classify.py", "symbol": "classify_consumers", "confidence": "unknown"}]})
    )

    result = _amu(["sweep", "--json"], tmp_path)

    findings = json.loads(result.stdout)["findings"]
    finding = next(finding for finding in findings if finding["path"] == "amu/classify.py")
    assert result.returncode == 0
    assert finding["symbol"] == "classify_consumers"
    assert finding["confidence"] == "unknown"


def test_done_exits_0_with_findings(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    _approve_report_only_lifecycle(tmp_path)
    r = _amu(["done", "--json"], tmp_path)
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["findings"] and rep["memory"]["entries"] >= 0


def test_done_refreshes_snapshot_and_records_sync_state(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    _approve_report_only_lifecycle(tmp_path)

    result = _amu(["done", "--json"], tmp_path)

    report = json.loads(result.stdout)
    sync_state = json.loads((tmp_path / ".amu" / "sync-state.json").read_text())
    assert result.returncode == 0
    assert (tmp_path / ".amu" / "snapshot.ndjson").exists()
    assert sync_state["snapshot_lines"] == 1 and report["snapshot_lines"] == 1


def test_done_docs_stage_counts_an_uncommitted_public_api_change(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "amu-done-test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "AMU done test"], cwd=tmp_path, check=True)
    source = tmp_path / "api.py"
    source.write_text("def public(value):\n    return value\n")
    subprocess.run(["git", "add", "api.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    source.write_text("def public(value, *, strict=False):\n    return value\n")
    _approve_report_only_lifecycle(tmp_path)

    result = _amu(["done", "--json"], tmp_path)

    payload = json.loads(result.stdout)
    # The recorded graph diff has three candidates.  The fourth is the
    # worktree-only API signature candidate, proving done shares docs' scan.
    assert result.returncode == 0
    assert payload["docs"]["candidates"] >= 4


def test_done_blocks_when_green_colours_have_only_legacy_check_history(fake_entire, tmp_path):
    """Old success-shaped rows cannot bypass the current evidence gate."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "amu-done-test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "AMU done test"], cwd=tmp_path, check=True)
    source = tmp_path / "src.py"
    source.write_text("def work(value):\n    return value\n")
    subprocess.run(["git", "add", "src.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    state = {
        "root": {"file": "src.py", "exports": 1, "internal": 0, "feature": "Core", "feature_source": "fixture"},
        "change": "body",
        "depth": 2,
        "nodes": [{"id": "n1", "path": "src.py", "symbol": "work", "confidence": "sound", "risk": "will_break", "feature": "Core", "order": 1, "action": "edit", "via": "fixture", "reason": "fixture", "verify": "pytest", "evidence": ["fixture"]}],
        "ripples": [],
        "features": {"Core": 1},
        "radius": {"files": 1, "packages": 1, "exports": 1, "consumers": 0, "tests": 0},
        "resolution": {"sound": 1, "guessed": 0, "unknown": 0, "qualifier": "fixture"},
        "next": "amu plan --approve",
        "map_hash": "done-refresh-map",
    }
    (tmp_path / ".amu").mkdir()
    (tmp_path / ".amu" / "map.json").write_text(json.dumps(state))
    (tmp_path / ".amu" / "contract.json").write_text(
        json.dumps(
            {
                "targets": [{"symbol": "src.py#work", "change_class": "body"}],
                "base_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip(),
                "map_hash": "done-refresh-map",
                "phases": [{"n": 1, "kind": "additive", "nodes": ["n1"], "files": ["src.py"], "tests": []}],
                "frozen_signatures": [],
                "allowed_files": ["src.py"],
                "created_at": "now",
            }
        )
    )
    (tmp_path / ".amu" / "state.json").write_text(json.dumps({"nodes": {"n1": "green"}, "violations": [], "history": [{"phase": 1, "violations": []}]}))
    source.write_text("def work(value):\n    return value + 1\n")

    result = _amu(["done", "--json"], tmp_path)

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["incomplete_phases"][0]["green"] is False
    assert "successful current check evidence" in payload["error"]
    assert payload["next"] == "amu check"


def test_sweep_fails_closed_when_graph_diff_is_unavailable(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["sweep", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert "graph diff failed" in payload["error"]
    assert payload["findings"] == []


def test_docs_fails_closed_when_graph_diff_is_unavailable(fake_entire, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["docs", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert "graph diff failed" in payload["error"]
    assert payload["exit"] == 3


def test_done_writes_an_incomplete_report_when_graph_diff_is_unavailable(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    _approve_report_only_lifecycle(tmp_path)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    saved = json.loads((tmp_path / ".amu" / "report.json").read_text())
    assert result.exit_code == 3
    assert payload == saved
    assert payload["complete"] is False
    assert payload["findings"] == []
    assert any(item["stage"] == "sweep" and "graph diff failed" in item["error"] for item in payload["errors"])
    assert "error" in payload["docs"]


def test_done_reports_a_later_docs_diff_failure_after_a_successful_sweep(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    _approve_report_only_lifecycle(tmp_path)
    real_diff = cli.entire.diff_json
    calls = 0

    def flaky_diff(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_diff(*args, **kwargs) if calls == 1 else None

    monkeypatch.setattr(cli.entire, "diff_json", flaky_diff)
    result = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert not any(item["stage"] == "sweep" for item in payload["errors"])
    assert any(item["stage"] == "docs" and "graph diff failed" in item["error"] for item in payload["errors"])
