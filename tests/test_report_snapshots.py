"""Same evidence, no recomputation; changed bindings and unsafe storage fail closed."""

import difflib
import subprocess
from dataclasses import replace

import pytest

from amu import report_store, value_flow
from amu.services import impact, mapping


def request(tmp_path):
    before = "def source():\n    return 1\n"
    (tmp_path / "api.py").write_text(before)
    (tmp_path / "test_api.py").write_text("from api import source\ndef test_source():\n    assert source()==1\n")
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(True),
            before.replace("return 1", "return 2").splitlines(True),
            fromfile="a/api.py",
            tofile="b/api.py",
        )
    )
    return impact.PatchRequest(patch, base="worktree")


def test_retrieval_reuses_flow_and_provider_and_keeps_complete_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("AMU_HOME", str(tmp_path.parent / (tmp_path.name + "-private")))
    req = replace(request(tmp_path), include_graph=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    counts = {"flow": 0, "graph": 0}
    original = value_flow.analyze_repository

    def analyze(*args, **kwargs):
        counts["flow"] += 1
        return original(*args, **kwargs)

    def inspect(*args, **kwargs):
        counts["graph"] += 1
        return {
            "map_hash": "snapshot-provider",
            "graph_evidence": {"at": "ignored", "edge_baselines": {}, "approvable": False},
            "nodes": [],
            "resolution": {},
        }

    monkeypatch.setattr(value_flow, "analyze_repository", analyze)
    monkeypatch.setattr(mapping, "inspect", inspect)
    initial = impact.execute_patch(str(tmp_path), req)
    sections = [
        impact.execute_patch(str(tmp_path), replace(req, section=s, page_size=100))
        for s in ["findings", "recommended_checks", "changes", "uncertainties"]
    ]
    assert {p["report_hash"] for p in sections} == {initial["report_hash"]}
    assert counts == {"flow": 1, "graph": 1}
    (tmp_path / "test_api.py").write_text("from api import source\ndef test_source():\n    assert source()==9\n")
    with pytest.raises(ValueError, match="stale"):
        impact.execute_patch(
            str(tmp_path), replace(req, section="findings", cursor=initial["retrieval"]["findings"]["cursor"])
        )
    assert counts == {"flow": 2, "graph": 2}


def test_cache_config_and_analyzer_identity_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("AMU_HOME", str(tmp_path.parent / (tmp_path.name + "-private")))
    req = request(tmp_path)
    initial = impact.execute_patch(str(tmp_path), req)
    key = report_store.fingerprint(tmp_path, req)
    cached = report_store.load(key)
    assert cached
    cache = (tmp_path.parent / (tmp_path.name + "-private") / "report-snapshots") / (key + ".json")
    cache.write_text('{"sha256":"false","snapshot":{}}')
    assert report_store.load(key) is None
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    assert report_store.fingerprint(tmp_path, req) != key
    with pytest.raises(ValueError, match="stale"):
        impact.execute_patch(
            str(tmp_path), replace(req, section="findings", cursor=initial["retrieval"]["findings"]["cursor"])
        )


def test_report_storage_limits_and_symlinks(tmp_path, monkeypatch):
    monkeypatch.setenv("AMU_HOME", str(tmp_path.parent / (tmp_path.name + "-private")))
    monkeypatch.setattr(report_store, "MAX_REPORTS", 2)
    for i in range(4):
        report_store.save(str(i) * 64, identity={}, groups={}, limitations=[], max_bytes=100, max_rows=1)
    assert len(list((tmp_path.parent / (tmp_path.name + "-private") / "report-snapshots").glob("*.json"))) == 2
    root = tmp_path.parent / (tmp_path.name + "-private") / "report-snapshots"
    (root / ("f" * 64 + ".json")).symlink_to(tmp_path / "secret")
    assert report_store.load("f" * 64) is None
