"""Raw configuration freshness hashing shares the secure state-file boundary."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from amu import freshness


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".amu").mkdir(mode=0o700)
    return root


def test_configuration_oversize_refuses_without_reading_full_content(repo, monkeypatch):
    (repo / ".amu/config.json").write_bytes(b"x" * 101)
    monkeypatch.setattr(freshness, "MAX_CONFIGURATION_BYTES", 100, raising=False)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("config hash must never allocate whole file"))
    digest, error = freshness._configuration(str(repo))
    assert digest is None and "byte limit" in error


def test_configuration_streaming_preserves_raw_digest_framing_and_modes(repo, monkeypatch):
    content = b'{ "not-normalized": 1 }\n'
    config = repo / ".amu/config.json"
    config.write_bytes(content)
    config.chmod(0o600)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("config hash must stream"))
    digest, error = freshness._configuration(str(repo))
    rows = [
        {"path": ".amu/config.json", "content_hash": hashlib.sha256(content).hexdigest(), "mode": 0o600},
        {"path": ".amu/feature_map.json", "content_hash": "missing"},
    ]
    assert error is None
    assert digest == hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def test_hardlinked_configuration_cannot_import_outside_content(repo, tmp_path):
    outside = tmp_path / "external.json"
    outside.write_bytes(b'"outside private bytes"')
    os.link(outside, repo / ".amu/config.json")
    digest, error = freshness._configuration(str(repo))
    assert digest is None and error
    assert "outside private bytes" not in error


def test_missing_configuration_directory_is_not_created(tmp_path):
    root = tmp_path / "no-state"
    root.mkdir()
    digest, error = freshness._configuration(str(root))
    rows = [{"path": path, "content_hash": "missing"} for path in (".amu/config.json", ".amu/feature_map.json")]
    assert error is None
    assert digest == hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    assert not (root / ".amu").exists()


def test_configuration_parent_symlink_is_rejected_without_read(repo, tmp_path, monkeypatch):
    (repo / ".amu").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config.json").write_bytes(b"outside values")
    (repo / ".amu").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("outside configuration must not be read"))
    digest, error = freshness._configuration(str(repo))
    assert digest is None and error


def test_configuration_file_symlink_is_rejected(repo, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside values")
    (repo / ".amu/config.json").symlink_to(outside)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and error


def test_configuration_byte_limit_exact_boundary_is_accepted(repo, monkeypatch):
    (repo / ".amu/config.json").write_bytes(b"x" * 100)
    (repo / ".amu/feature_map.json").write_bytes(b"y" * 100)
    monkeypatch.setattr(freshness, "MAX_CONFIGURATION_BYTES", 100, raising=False)
    digest, error = freshness._configuration(str(repo))
    assert digest and error is None


def test_configuration_reads_are_bounded_chunks(repo, monkeypatch):
    config = repo / ".amu/config.json"
    config.write_bytes(b"x" * 100)
    identity = config.stat()
    original_read = os.read
    requested = []

    def read(descriptor, size):
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            requested.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr(freshness, "CONFIGURATION_READ_CHUNK_BYTES", 7)
    monkeypatch.setattr(os, "read", read)
    digest, error = freshness._configuration(str(repo))
    assert digest and error is None
    assert requested and max(requested) <= 7


def test_configuration_growth_after_initial_stat_hits_read_bound(repo, monkeypatch):
    config = repo / ".amu/config.json"
    config.write_bytes(b"x" * 100)
    original_read = os.read
    grew = False

    def read(descriptor, size):
        nonlocal grew
        if not grew:
            grew = True
            config.write_bytes(b"x" * 101)
        return original_read(descriptor, size)

    monkeypatch.setattr(freshness, "MAX_CONFIGURATION_BYTES", 100)
    monkeypatch.setattr(os, "read", read)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and "byte limit" in error


def test_configuration_deadline_includes_reading(repo, monkeypatch):
    (repo / ".amu/config.json").write_bytes(b"x" * 100)
    original_read = os.read

    def slow_read(descriptor, size):
        time.sleep(0.04)
        return original_read(descriptor, size)

    monkeypatch.setattr(freshness, "CONFIGURATION_CAPTURE_SECONDS", 0.03)
    monkeypatch.setattr(os, "read", slow_read)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and "deadline" in error


def test_configuration_replacement_after_open_refuses_old_descriptor_hash(repo, monkeypatch):
    config = repo / ".amu/config.json"
    config.write_bytes(b"original")
    original_read = os.read
    replaced = False

    def read(descriptor, size):
        nonlocal replaced
        chunk = original_read(descriptor, size)
        if not replaced:
            replaced = True
            replacement = repo / ".amu/new.json"
            replacement.write_bytes(b"modified")
            replacement.replace(config)
        return chunk

    monkeypatch.setattr(os, "read", read)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and "changed" in error


def test_configuration_parent_swap_is_rejected_without_reading_outside(repo, tmp_path, monkeypatch):
    config = repo / ".amu/config.json"
    config.write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "config.json"
    external.write_bytes(b"outside secret")
    identity = external.stat()
    original_regular = freshness.state_io._regular
    original_read = os.read
    swapped = False

    def swap_after_open(path, info, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            (repo / ".amu").rename(repo / "original-amu")
            (repo / ".amu").symlink_to(outside, target_is_directory=True)
        return original_regular(path, info, **kwargs)

    def read(descriptor, size):
        current = os.fstat(descriptor)
        assert (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
        return original_read(descriptor, size)

    monkeypatch.setattr(freshness.state_io, "_regular", swap_after_open)
    monkeypatch.setattr(os, "read", read)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and error
    assert "outside secret" not in error


def test_configuration_owner_or_writable_managed_directory_is_rejected(repo):
    (repo / ".amu/config.json").write_bytes(b"content")
    (repo / ".amu").chmod(0o777)
    digest, error = freshness._configuration(str(repo))
    assert digest is None and "not writable by others" in error
