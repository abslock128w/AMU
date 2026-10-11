"""End-to-end map regressions for independent Python source evidence."""

from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from amu import cli


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def _impact(*, callee: bool = False, caller_line: int = 4) -> dict:
    return {
        "focus": {
            "id": "target-id",
            "name": "target",
            "qualified_name": "target",
            "file_path": "target.py",
            "start_line": 1,
            "end_line": 3,
        },
        "callers": {
            "total": 1,
            "entries": [
                {
                    "endpoint": {"name": "consumer", "qualified_name": "consumer", "file_path": "consumer.py"},
                    "relation": "CALLS",
                    "depth": 1,
                    "call_site": {"file_path": "consumer.py", "line": caller_line},
                }
            ],
        },
        "callees": {
            "total": int(callee),
            "entries": (
                [
                    {
                        "endpoint": {"name": "helper", "qualified_name": "helper", "file_path": "target.py"},
                        "relation": "CALLS",
                        "depth": 1,
                    }
                ]
                if callee
                else []
            ),
        },
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
        "stats": {"completeness_level": "ok"},
    }


def _write_repo_files(tmp_path):
    (tmp_path / "target.py").write_text(
        "def target():\n"
        '    """Example only: helper()."""\n'
        "    return 0\n\n"
        "def helper():\n"
        "    return 1\n\n"
        "def sibling():\n"
        "    return helper()\n"
    )
    (tmp_path / "consumer.py").write_text("from target import target\n\ndef consumer():\n    return target()\n")
    subprocess.run(["git", "add", "target.py", "consumer.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=amu@example.test", "-c", "user.name=AMU", "commit", "-qm", "source baseline"],
        cwd=tmp_path,
        check=True,
    )


def test_map_keeps_a_real_caller_sound_when_source_evidence_confirms_it(fake_entire, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _write_repo_files(tmp_path)
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _impact())

    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "target.py#target", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["graph_evidence"]["approvable"] is True
    assert payload["graph_evidence"]["contradicted_python_calls"] == []
    assert any(node["path"] == "consumer.py" and node["confidence"] == "sound" for node in payload["nodes"])


def test_map_removes_docstring_only_callee_and_refuses_approval(fake_entire, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _write_repo_files(tmp_path)
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _impact(callee=True))

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "target.py#target", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["approvable"] is False
    assert map_payload["graph_evidence"]["contradicted_python_calls"] == [
        {
            "target": "target",
            "section": "callees",
            "source_path": "target.py",
            "endpoint": "helper",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]
    assert not any(
        node["path"] == "target.py" and node["symbol"] == "helper" and node["confidence"] == "sound"
        for node in map_payload["nodes"]
    )
    assert not any(
        ripple["endpoint"]["symbol"] == "helper" and ripple["confidence"] == "sound"
        for ripple in map_payload["ripples"]
    )
    assert (
        map_payload["resolution"]["source_contradictions"] == map_payload["graph_evidence"]["contradicted_python_calls"]
    )
    assert planned.exit_code == 2
    assert "source AST contradicted" in plan_payload["approval_refused"]


def test_map_removes_docstring_only_caller_and_refuses_approval(fake_entire, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _write_repo_files(tmp_path)
    (tmp_path / "consumer.py").write_text('def consumer():\n    """Example only: target()."""\n    return 0\n')
    subprocess.run(["git", "add", "consumer.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=amu@example.test", "-c", "user.name=AMU", "commit", "-qm", "docstring caller fixture"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _impact(caller_line=2))

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "target.py#target", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["contradicted_python_calls"] == [
        {
            "target": "target",
            "section": "callers",
            "source_path": "consumer.py",
            "endpoint": "consumer",
            "line": 2,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]
    assert not any(node["path"] == "consumer.py" and node["confidence"] == "sound" for node in map_payload["nodes"])
    assert not any(
        ripple["endpoint"]["symbol"] == "consumer" and ripple["confidence"] == "sound"
        for ripple in map_payload["ripples"]
    )
    assert planned.exit_code == 2
    assert "source AST contradicted" in plan_payload["approval_refused"]


def test_map_rejects_a_same_named_call_resolved_to_a_different_module(fake_entire, tmp_path, monkeypatch):
    """``other.target()`` cannot validate an edge to ``target.py#target``."""
    _init_repo(tmp_path)
    (tmp_path / "target.py").write_text("def target():\n    return 'expected'\n")
    (tmp_path / "other.py").write_text("def target():\n    return 'other'\n")
    (tmp_path / "consumer.py").write_text("import other\n\ndef consumer():\n    return other.target()\n")
    subprocess.run(["git", "add", "target.py", "other.py", "consumer.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=amu@example.test", "-c", "user.name=AMU", "commit", "-qm", "collision fixture"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _impact())

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "target.py#target", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["approvable"] is False
    assert map_payload["graph_evidence"]["contradicted_python_calls"] == [
        {
            "target": "target",
            "section": "callers",
            "source_path": "consumer.py",
            "endpoint": "consumer",
            "line": 4,
            "evidence": "source:python-ast:call-target-identity-mismatch",
        }
    ]
    assert not any(node["path"] == "consumer.py" and node["confidence"] == "sound" for node in map_payload["nodes"])
    assert planned.exit_code == 2
    assert "source AST contradicted" in plan_payload["approval_refused"]
