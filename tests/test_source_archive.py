"""Source-release support completeness and no-extraction archive guards."""

import gzip
import io
import tarfile
from pathlib import Path

import pytest
from setuptools._distutils.filelist import FileList

from scripts import check_source_archive as checker


def _archive(tmp_path, *, missing=None, extra=None, constraints=b"rich==15.0.0\n", executable=True):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        root = tarfile.TarInfo("amu-0.2.0")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for relative in sorted(checker.REQUIRED_FILES - {missing}):
            body = constraints if relative == "constraints.txt" else b"Representative source support.\n"
            if relative == "tests/__init__.py":
                body = b""
            member = tarfile.TarInfo("amu-0.2.0/" + relative)
            member.size = len(body)
            if relative == "tests/bin/entire" and executable:
                member.mode = 0o755
            archive.addfile(member, io.BytesIO(body))
        if extra is not None:
            archive.addfile(extra, io.BytesIO(b"x" * extra.size) if extra.isreg() else None)
    return path


def test_valid_representative_archive_without_extracting(tmp_path):
    path = _archive(tmp_path)
    report = checker.check_archive(path)
    assert report["root"] == "amu-0.2.0"
    assert report["files"] == report["required_files"] == len(checker.REQUIRED_FILES)
    assert len(report["constraints_sha256"]) == 64
    assert sorted(child.name for child in tmp_path.iterdir()) == ["source.tar.gz"]


@pytest.mark.parametrize("missing", ["tests/conftest.py", "tests/fake_entire.py", "tests/__init__.py",
                                    "tests/bin/entire", "tests/fixtures/symbols.ndjson",
                                    "scripts/smoke_installed.py", "constraints.txt",
                                    "web/public/vendor/d3.min.js", "web/public/vendor/d3.LICENSE", "THIRD-PARTY.txt"])
def test_missing_release_support_is_failure(tmp_path, missing):
    with pytest.raises(checker.ArchiveError, match="missing required release support") as caught:
        checker.check_archive(_archive(tmp_path, missing=missing))
    assert missing in str(caught.value)


@pytest.mark.parametrize("name", ["/outside", "amu-0.2.0/../outside", "amu-0.2.0/./hidden",
                                 "amu-0.2.0//hidden", "amu-0.2.0/\\outside", "amu-0.2.0/C:outside",
                                 "amu-0.2.0/a\nb", "another-root/file"])
def test_unsafe_or_multiple_roots_rejected(tmp_path, name):
    with pytest.raises(checker.ArchiveError, match="unsafe archive path|multiple root"):
        checker.check_archive(_archive(tmp_path, extra=tarfile.TarInfo(name)))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_links_and_special_files_rejected(tmp_path, kind):
    member = tarfile.TarInfo("amu-0.2.0/untrusted")
    member.type = kind
    member.linkname = "../../outside"
    with pytest.raises(checker.ArchiveError, match="links, sparse files and special"):
        checker.check_archive(_archive(tmp_path, extra=member))


@pytest.mark.parametrize("relative", [".amu/config.json", "web/node_modules/pkg/index.js", "tests/__pycache__/file.pyc",
                                     ".git/config", ".env", "docs/evidence/release-candidate-2026.json"])
def test_generated_private_and_candidate_sidecars_rejected(tmp_path, relative):
    with pytest.raises(checker.ArchiveError, match="generated/private state"):
        checker.check_archive(_archive(tmp_path, extra=tarfile.TarInfo("amu-0.2.0/" + relative)))


@pytest.mark.parametrize("body", [b"", b"rich>=15\n", b"rich==15\nrich==16\n", b"\xff"])
def test_archived_constraints_must_be_nonempty_exact_unique_utf8_pins(tmp_path, body):
    with pytest.raises(checker.ArchiveError, match="constraints|empty required"):
        checker.check_archive(_archive(tmp_path, constraints=body))


def test_duplicate_member_rejected(tmp_path):
    with pytest.raises(checker.ArchiveError, match="duplicate member"):
        checker.check_archive(_archive(tmp_path, extra=tarfile.TarInfo("amu-0.2.0/README.md")))


def test_fake_provider_support_preserves_executable_mode(tmp_path):
    with pytest.raises(checker.ArchiveError, match="entry point is not executable"):
        checker.check_archive(_archive(tmp_path, executable=False))


def test_member_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "MAX_MEMBERS", 2)
    with pytest.raises(checker.ArchiveError, match="member-count limit"):
        checker.check_archive(_archive(tmp_path))


def test_member_bytes_are_bounded(tmp_path, monkeypatch):
    path = _archive(tmp_path)
    monkeypatch.setattr(checker, "MAX_MEMBER_BYTES", 2)
    with pytest.raises(checker.ArchiveError, match="member exceeds size limit"):
        checker.check_archive(path)


def test_compressed_bytes_are_bounded(tmp_path, monkeypatch):
    path = _archive(tmp_path)
    monkeypatch.setattr(checker, "MAX_ARCHIVE_BYTES", 2)
    with pytest.raises(checker.ArchiveError, match="compressed byte limit"):
        checker.check_archive(path)


def test_expansion_including_metadata_is_bounded(tmp_path, monkeypatch):
    member = tarfile.TarInfo("amu-0.2.0/" + "long" * 100)
    path = _archive(tmp_path, extra=member)
    monkeypatch.setattr(checker, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(checker.ArchiveError, match="decompressed byte limit"):
        checker.check_archive(path)


def test_archive_path_symlink_rejected(tmp_path):
    path = _archive(tmp_path)
    link = tmp_path / "linked.tar.gz"
    link.symlink_to(path)
    with pytest.raises(checker.ArchiveError, match="cannot safely read"):
        checker.check_archive(link)


def test_invalid_gzip_rejected(tmp_path):
    path = tmp_path / "broken.tar.gz"
    path.write_bytes(b"not a gzip archive")
    with pytest.raises(checker.ArchiveError, match="cannot safely read"):
        checker.check_archive(path)


def test_nonpadding_data_after_first_archive_rejected(tmp_path):
    path = _archive(tmp_path)
    contents = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(contents + b"uninspected appended payload"))
    with pytest.raises(checker.ArchiveError, match="non-padding trailing data"):
        checker.check_archive(path)


def test_truncated_compressed_archive_rejected(tmp_path):
    path = _archive(tmp_path)
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(checker.ArchiveError, match="cannot safely read"):
        checker.check_archive(path)


def test_manifest_includes_support_and_excludes_generated_private_files():
    root = Path(__file__).resolve().parents[1]
    supported = {name for name in checker.REQUIRED_FILES
                 if name.startswith(("tests/", "scripts/", "docs/", "web/", ".github/"))
                 or name in {"constraints.txt", "THIRD-PARTY.txt"}}
    excluded = {".amu/config.json", "web/node_modules/dep/index.js", "tests/__pycache__/file.pyc",
                "tests/nested/.amu/config.json", "tests/_nobin/entire",
                "docs/evidence/release-candidate-2026.json"}
    selected = FileList()
    selected.set_allfiles(sorted(supported | excluded))
    for line in (root / "MANIFEST.in").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            selected.process_template_line(line)
    assert supported <= set(selected.files)
    assert not excluded & set(selected.files)
