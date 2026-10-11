"""Command-contract tests for the thin Entire CLI wrapper."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from amu import entire


def _result(stdout="", stderr="", code=0, sink=None):
    if sink:
        sink(stdout.encode())
    return SimpleNamespace(returncode=code, stdout=b"" if sink else stdout.encode(), stderr=stderr.encode(), error=None)


def _wire(monkeypatch, output):
    monkeypatch.setattr(entire.process, "run", lambda _command, **kwargs: _result(output, sink=kwargs.get("stdout_sink")))


def test_capabilities_uses_only_the_flags_supported_by_the_cli(monkeypatch):
    calls = []

    def fake_run(command, *, stdout_limit, stderr_limit, timeout):
        calls.append((command, stdout_limit, stderr_limit, timeout))
        return _result('{"semantic_languages":["Python"]}')

    monkeypatch.setattr(entire.process, "run", fake_run)

    assert entire.capabilities("/a/repository") == {"semantic_languages": ["Python"]}
    assert calls == [(["entire", "graph", "capabilities", "--json"], entire.MAX_QUERY_BYTES, entire.MAX_STDERR_BYTES, 60)]


def test_capabilities_surfaces_a_cli_failure_without_falling_back(monkeypatch):
    def fake_run(command, *, stdout_limit, stderr_limit, timeout):
        assert command == ["entire", "graph", "capabilities", "--json"]
        assert (stdout_limit, stderr_limit, timeout) == (entire.MAX_QUERY_BYTES, entire.MAX_STDERR_BYTES, 60)
        return _result(stderr="capabilities unavailable", code=2)

    monkeypatch.setattr(entire.process, "run", fake_run)

    assert entire.capabilities() == {"error": "capabilities unavailable", "rc": 2}


def test_invalid_subprocess_arguments_degrade_without_launch_or_traceback():
    result = entire.impact_json(".", "bad\0symbol")
    assert result["error"] and result["complete"] is False


def test_provider_exit_code_is_not_mislabeled_as_a_transport_limit(monkeypatch):
    monkeypatch.setattr(entire.process, "run", lambda *_args, **_kwargs: _result(stderr="provider failed", code=125))
    result = entire.index(".")
    assert result["rc"] == 125 and result.get("error_type") != "output_limit"


def test_impact_queries_the_live_worktree_by_default_and_head_only_when_requested(monkeypatch):
    calls = []

    def fake_run(command, *, stdout_limit, stderr_limit, timeout):
        calls.append(command)
        return _result("{}")

    monkeypatch.setattr(entire.process, "run", fake_run)

    assert entire.impact_json("/a/repository", "target") == {}
    assert entire.impact_json("/a/repository", "target", head=True) == {}
    assert "--head" not in calls[0]
    assert calls[1][-1] == "--head"


def test_snapshot_reads_the_worktree_only_when_requested(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, *, stdout_limit, stderr_limit, timeout, stdout_sink, capture_stdout):
        calls.append((command, stdout_limit, stderr_limit, timeout, capture_stdout))
        return _result('{"record_type":"file"}\n', sink=stdout_sink)

    monkeypatch.setattr(entire.process, "run", fake_run)

    result = entire.snapshot("/a/repository", str(tmp_path / "worktree.ndjson"), worktree=True)

    assert result == {"lines": 1, "path": str(tmp_path / "worktree.ndjson")}
    assert (tmp_path / "worktree.ndjson").read_text() == '{"record_type":"file"}\n'
    assert calls == [
        (
            [
                "entire",
                "graph",
                "snapshot",
                "--repo",
                "/a/repository",
                "--format",
                "compact-ndjson",
                "--worktree",
            ],
            entire.MAX_SNAPSHOT_BYTES,
            entire.MAX_STDERR_BYTES,
            300,
            False,
        )
    ]


def test_snapshot_keeps_head_behavior_when_worktree_is_not_requested(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, *, stdout_limit, stderr_limit, timeout, stdout_sink, capture_stdout):
        calls.append(command)
        return _result('{"record_type":"header"}\n', sink=stdout_sink)

    monkeypatch.setattr(entire.process, "run", fake_run)

    assert entire.snapshot("/a/repository", str(tmp_path / "head.ndjson")) == {
        "lines": 1,
        "path": str(tmp_path / "head.ndjson"),
    }
    assert calls == [["entire", "graph", "snapshot", "--repo", "/a/repository", "--format", "compact-ndjson"]]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "no records"),
        ("not-json\n", "invalid NDJSON"),
        ("[]\n", "malformed compact"),
        ('{"error":"provider failed"}\n', "untyped record"),
    ],
)
def test_snapshot_rejects_empty_or_malformed_output_without_replacing_prior_evidence(tmp_path, monkeypatch, output, message):
    destination = tmp_path / "snapshot.ndjson"
    destination.write_text('{"record_type":"header","old":true}\n')

    _wire(monkeypatch, output)

    result = entire.snapshot("/a/repository", str(destination), worktree=True)

    assert message in result["error"]
    assert destination.read_text() == '{"record_type":"header","old":true}\n'


def test_snapshot_replaces_only_after_complete_new_file_is_durable(tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.ndjson"
    old = '{"record_type":"header","old":true}\n'
    fresh = '["h",1,{"schema_version":"1.1"}]\n["f",1,2,3,4]\n'
    destination.write_text(old)
    real_replace = os.replace
    replacements = []

    _wire(monkeypatch, fresh)

    def assert_atomic_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        replacements.append((source_path, target_path))
        assert source_path != target_path
        assert source_path.parent == destination.parent
        assert source_path.read_text() == fresh
        # The old snapshot remains available right until the single replace.
        assert target_path.read_text() == old
        real_replace(source_path, target_path)

    monkeypatch.setattr(entire.os, "replace", assert_atomic_replace)

    result = entire.snapshot("/a/repository", str(destination))

    assert result == {"lines": 2, "path": str(destination)}
    assert destination.read_text() == fresh
    assert len(replacements) == 1
    assert replacements[0][1] == destination
    assert not list(tmp_path.glob(".snapshot.ndjson.*"))


def test_snapshot_accepts_entire_compact_ndjson_wire_records(tmp_path, monkeypatch):
    output = '["h",1,{"schema_version":"1.1"}]\n["d",1,["file-id","a.py"]]\n["f",1,2,3,4]\n'
    destination = tmp_path / "snapshot.ndjson"
    _wire(monkeypatch, output)

    result = entire.snapshot("/a/repository", str(destination), worktree=True)

    assert result == {"lines": 3, "path": str(destination)}
    assert destination.read_text() == output


def test_snapshot_replace_failure_preserves_prior_evidence_and_cleans_temporary_file(tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.ndjson"
    old = '{"record_type":"header","old":true}\n'
    destination.write_text(old)
    _wire(monkeypatch, '{"record_type":"header"}\n')

    def fail_replace(_source, _target):
        raise OSError("disk unavailable")

    monkeypatch.setattr(entire.os, "replace", fail_replace)

    result = entire.snapshot("/a/repository", str(destination))

    assert "could not persist" in result["error"]
    assert destination.read_text() == old
    assert not list(tmp_path.glob(".snapshot.ndjson.*"))
