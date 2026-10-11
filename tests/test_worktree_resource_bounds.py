"""Worktree capture refuses resource gaps rather than describing a clean repo."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from amu import state


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _failed(value, text):
    assert value["digest"] is None
    assert value["error"] and text in value["error"].lower()
    assert value["evidence_version"] == 1


def test_file_byte_limit_refuses_before_whole_file_read(repo, monkeypatch):
    (repo / "large.bin").write_bytes(b"x" * 101)
    monkeypatch.setattr(state, "MAX_WORKTREE_FILE_BYTES", 100, raising=False)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("capture must not allocate whole files"))
    _failed(state.worktree_fingerprint(str(repo)), "file byte limit")


def test_small_file_is_hashed_without_read_bytes(repo, monkeypatch):
    (repo / "small.bin").write_bytes(b"x" * 50)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("capture must stream file bytes"))
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None and result["digest"]
    assert result["files"] == ["small.bin"]


def test_aggregate_byte_limit_never_returns_partial_digest(repo, monkeypatch):
    (repo / "a.bin").write_bytes(b"a" * 60)
    (repo / "b.bin").write_bytes(b"b" * 60)
    monkeypatch.setattr(state, "MAX_WORKTREE_TOTAL_BYTES", 100, raising=False)
    _failed(state.worktree_fingerprint(str(repo)), "aggregate byte limit")


def test_dirty_file_count_limit_refuses_instead_of_dropping_rows(repo, monkeypatch):
    (repo / "a.bin").write_bytes(b"a")
    (repo / "b.bin").write_bytes(b"b")
    monkeypatch.setattr(state, "MAX_WORKTREE_FILES", 1, raising=False)
    _failed(state.worktree_fingerprint(str(repo)), "file count limit")


def test_git_status_output_has_a_real_process_byte_limit(repo, monkeypatch):
    (repo / ("a" * 80 + ".bin")).write_bytes(b"a")
    monkeypatch.setattr(state, "MAX_WORKTREE_STATUS_BYTES", 20, raising=False)
    _failed(state.worktree_fingerprint(str(repo)), "status output byte limit")


def test_exact_file_and_aggregate_boundaries_are_accepted(repo, monkeypatch):
    (repo / "a.bin").write_bytes(b"a" * 100)
    (repo / "b.bin").write_bytes(b"b" * 100)
    monkeypatch.setattr(state, "MAX_WORKTREE_FILE_BYTES", 100, raising=False)
    monkeypatch.setattr(state, "MAX_WORKTREE_TOTAL_BYTES", 200, raising=False)
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None and result["digest"]
    assert result["files"] == ["a.bin", "b.bin"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX non-UTF8 filename bytes")
def test_git_status_non_utf8_filename_keeps_surrogateescape_identity(repo):
    name = b"undecodable-\xff.bin"
    try:
        descriptor = os.open(os.fsencode(repo) + b"/" + name, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        pytest.skip("filesystem does not permit non-UTF8 filename bytes")
    os.write(descriptor, b"content")
    os.close(descriptor)
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None and result["digest"]
    assert result["files"] == [os.fsdecode(name)]


def test_legacy_digest_framing_is_preserved(repo):
    content = b"stable source bytes\n"
    (repo / "source.py").write_bytes(content)
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None
    change = {"path": "source.py", "status": "??", "old_path": None}
    expected = hashlib.sha256()
    expected.update(json.dumps(change, sort_keys=True).encode())
    expected.update(b"\0")
    expected.update(json.dumps(result["records"][0], sort_keys=True).encode())
    expected.update(b"\0")
    expected.update(content)
    expected.update(b"\0")
    assert result["digest"] == expected.hexdigest()[:24]
    assert result["records"][0]["content_hash"] == hashlib.sha256(content).hexdigest()


def test_regular_file_reads_use_fixed_chunks_and_two_stable_passes(repo, monkeypatch):
    source = repo / "source.bin"
    source.write_bytes(b"x" * 100)
    identity = source.stat()
    original_read = os.read
    requested = []
    observed = 0

    def read(descriptor, maximum):
        nonlocal observed
        current = os.fstat(descriptor)
        is_source = (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
        if is_source:
            requested.append(maximum)
        chunk = original_read(descriptor, maximum)
        if is_source:
            observed += len(chunk)
        return chunk

    monkeypatch.setattr(state, "WORKTREE_READ_CHUNK_BYTES", 7)
    monkeypatch.setattr(os, "read", read)
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None and result["digest"]
    assert requested and max(requested) <= 7
    assert observed == 200


def test_capture_deadline_covers_content_hashing_not_only_git_status(repo, monkeypatch):
    (repo / "source.bin").write_bytes(b"x" * 100)
    original_stream = state._worktree_stream_hash

    def slow_stream(*args, **kwargs):
        time.sleep(0.04)
        return original_stream(*args, **kwargs)

    monkeypatch.setattr(state, "WORKTREE_CAPTURE_SECONDS", 0.03)
    monkeypatch.setattr(state, "_worktree_stream_hash", slow_stream)
    _failed(state.worktree_fingerprint(str(repo)), "deadline")


def test_growing_file_is_refused_by_stream_bound_even_after_valid_stat(repo, monkeypatch):
    source = repo / "source.bin"
    source.write_bytes(b"x" * 100)
    original_stream = state._worktree_stream_hash

    def growing_stream(*args, **kwargs):
        source.write_bytes(b"x" * 101)
        return original_stream(*args, **kwargs)

    monkeypatch.setattr(state, "MAX_WORKTREE_FILE_BYTES", 100)
    monkeypatch.setattr(state, "_worktree_stream_hash", growing_stream)
    _failed(state.worktree_fingerprint(str(repo)), "byte limit")


def test_equal_size_mutation_between_digest_passes_never_gets_a_digest(repo, monkeypatch):
    source = repo / "source.bin"
    source.write_bytes(b"original")
    before = source.stat()
    original_seek = os.lseek

    def mutate_between_passes(descriptor, position, mode):
        result = original_seek(descriptor, position, mode)
        source.write_bytes(b"modified")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        return result

    monkeypatch.setattr(os, "lseek", mutate_between_passes)
    _failed(state.worktree_fingerprint(str(repo)), "changed")


def test_path_replacement_after_open_invalidates_capture(repo, monkeypatch):
    source = repo / "source.bin"
    source.write_bytes(b"original")
    original_stream = state._worktree_stream_hash
    replaced = False

    def replace_after_pass(*args, **kwargs):
        nonlocal replaced
        result = original_stream(*args, **kwargs)
        if not replaced:
            replaced = True
            replacement = repo / "replacement.bin"
            replacement.write_bytes(b"original")
            replacement.replace(source)
        return result

    monkeypatch.setattr(state, "_worktree_stream_hash", replace_after_pass)
    _failed(state.worktree_fingerprint(str(repo)), "changed")


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor-relative nofollow traversal")
def test_parent_symlink_swap_cannot_read_outside_repository(repo, tmp_path, monkeypatch):
    source_dir = repo / "sub"
    source_dir.mkdir()
    (source_dir / "source.bin").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.bin").write_bytes(b"outside secret")
    original_open = state._open_worktree_file

    def swap_parent(root, resolved):
        source_dir.rename(repo / "original-sub")
        source_dir.symlink_to(outside, target_is_directory=True)
        return original_open(root, resolved)

    monkeypatch.setattr(state, "_open_worktree_file", swap_parent)
    result = state.worktree_fingerprint(str(repo))
    assert result["digest"] is None and result["error"]
    assert result["failed_path"] == "sub/source.bin"


def test_non_utf8_git_filename_identity_is_not_discarded_on_missing_file(repo, monkeypatch):
    output = b"?? bad-\xff.bin\0"
    monkeypatch.setattr(state.process, "run", lambda *_args, **_kwargs: state.process.Result(0, output, b"", None, len(output), 0))
    result = state.worktree_fingerprint(str(repo))
    assert result["digest"] is None and result["error"]
    assert result["files"] == ["bad-\udcff.bin"]
    assert result["failed_path"] == "bad-\udcff.bin"


def test_status_stderr_output_limit_is_not_a_clean_capture(repo, monkeypatch):
    monkeypatch.setattr(state.process, "run", lambda *_args, **_kwargs: state.process.Result(125, b"", b"", "output_limit", 0, 1025))
    _failed(state.worktree_fingerprint(str(repo)), "status output byte limit")


def test_git_status_does_not_execute_repository_fsmonitor_hook(repo, tmp_path):
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "fsmonitor.py"
    hook.write_text(
        f"#!{sys.executable}\nimport pathlib,sys\npathlib.Path({str(marker)!r}).write_text('ran')\n"
        "sys.stdout.buffer.write(b'fake-token\\x00')\n"
    )
    hook.chmod(0o700)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)
    (repo / "source.py").write_bytes(b"x")
    result = state.worktree_fingerprint(str(repo))
    assert result["error"] is None and result["digest"]
    assert not marker.exists(), "read-only status must disable repository fsmonitor execution"
