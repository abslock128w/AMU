"""Static API evidence remains attached to human-reviewed docs candidates."""

import copy
import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, docs


def _repo(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, source in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for args in [["init", "-q"], ["config", "user.email", "docs-api@example.invalid"], ["config", "user.name", "Docs API test"], ["add", "."], ["commit", "-qm", "baseline"]]:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.parametrize("before,after,symbol,change,facet", [
    ('def public(value=3): return value\n', 'def public(value=4): return value\n', "public", "signature", "defaults"),
    ('def public(): return 1\n', 'async def public(): return 1\n', "public", "signature", "async"),
    ('def _internal(value=3): return value\npublic = _internal\n', 'def _internal(value=4): return value\npublic = _internal\n', "public", "surface", "defaults"),
    ('from .first import helper as public\n', 'from .second import helper as public\n', "public", "surface", "reexports"),
    ('from typing import overload\n@overload\ndef public(value: int): ...\ndef public(value): return value\n', 'from typing import overload\n@overload\ndef public(value: float): ...\ndef public(value): return value\n', "public", "signature", "overloads"),
])
def test_manifest_changes_keep_precise_review_evidence(tmp_path, before, after, symbol, change, facet):
    repo = _repo(tmp_path, {"api.py": before})
    (repo / "api.py").write_text(after)
    rows = [row for row in docs.worktree_candidates(str(repo), {}) if row["candidate"] == f"api.py#{symbol}"]
    assert len(rows) == 1
    row = rows[0]
    assert row["api_change"] == change
    assert facet in row["facets_changed"]
    assert row["evidence"] == "worktree-api-manifest"
    assert row["confidence"] == "unknown"
    assert row["api_evidence"]["before"]["signature_fingerprint"]
    assert docs.packet(row, str(repo))["facets_changed"] == row["facets_changed"]


@pytest.mark.parametrize("field,value", [
    ("api_change", "behavior"), ("facets_changed", ["exceptions"]),
    ("api_evidence", {"before": {"signature_fingerprint": "changed"}}),
])
def test_candidate_identity_binds_manifest_assessment_and_source_hashes(field, value):
    candidate = {"candidate": "api.py#public", "class": "body", "confidence": "unknown", "targets": [], "api_change": "surface", "facets_changed": ["defaults"], "api_evidence": {"before": {"signature_fingerprint": "original"}}}
    changed = copy.deepcopy(candidate)
    changed[field] = value
    assert docs.candidate_fingerprint(candidate) != docs.candidate_fingerprint(changed)


def test_redacted_default_changes_have_distinct_candidate_identities(tmp_path):
    source = 'def public(api_key: str = "secret-before"): return None\n'
    repo = _repo(tmp_path, {"api.py": source})
    path = repo / "api.py"
    path.write_text(source.replace("secret-before", "secret-one"))
    first = next(row for row in docs.worktree_candidates(str(repo), {}) if row["candidate"] == "api.py#public")
    path.write_text(source.replace("secret-before", "secret-two"))
    second = next(row for row in docs.worktree_candidates(str(repo), {}) if row["candidate"] == "api.py#public")
    assert first["old"] == second["old"]
    assert first["new"] == second["new"]
    assert first["fingerprint"] != second["fingerprint"]
    assert "secret-" not in json.dumps([first, second])


@pytest.mark.parametrize("path,before,after,symbol", [
    ("settings.json", '{"timeout": 3, "internal": 1}', '{"timeout": 4, "internal": 2}', "timeout"),
    ("settings.toml", 'timeout = 3\ninternal = 1\n', 'timeout = 4\ninternal = 2\n', "timeout"),
])
def test_only_explicitly_documented_config_keys_become_candidates(tmp_path, path, before, after, symbol):
    repo = _repo(tmp_path, {path: before})
    (repo / path).write_text(after)
    assert docs.worktree_candidates(str(repo), {}) == []
    mapping = {f"{path}#{symbol}": [{"file": "docs/config.md", "section": "## Settings"}]}
    rows = docs.worktree_candidates(str(repo), mapping)
    assert [row["candidate"] for row in rows] == [f"{path}#{symbol}"]
    assert rows[0]["api_change"] == "surface"
    assert rows[0]["confidence"] == "unknown"
    assert rows[0]["targets"] == mapping[f"{path}#{symbol}"]


def test_mapped_config_parse_failure_remains_an_explicit_unknown(tmp_path):
    repo = _repo(tmp_path, {"settings.json": '{"timeout": 3}'})
    (repo / "settings.json").write_text('{"timeout":')
    mapping = {"settings.json#timeout": [{"file": "docs/config.md", "section": "## Settings"}]}
    rows = docs.worktree_candidates(str(repo), mapping)
    assert len(rows) == 1
    assert rows[0]["candidate"] == "settings.json#*"
    assert rows[0]["confidence"] == "unknown"
    assert "unambiguously" in rows[0]["reason"]


def test_config_wildcard_mapping_opts_in_added_and_removed_keys(tmp_path):
    repo = _repo(tmp_path, {"settings.json": '{"old": 1}'})
    (repo / "settings.json").write_text('{"new": 2}')
    target = [{"file": "docs/config.md", "section": "## Settings"}]
    rows = docs.worktree_candidates(str(repo), {"settings.json#*": target})
    found = {row["candidate"]: row for row in rows}
    assert set(found) == {"settings.json#old", "settings.json#new"}
    assert found["settings.json#old"]["api_change"] == "removed"
    assert found["settings.json#new"]["api_change"] == "added"
    assert all(row["targets"] == target for row in rows)


def test_manifest_candidates_never_auto_write_mapped_docs(fake_entire, tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"api.py": 'def public(value=3): return value\n', "docs/api.md": '## API\nOriginal prose.\n'})
    (repo / "api.py").write_text('def public(value=4): return value\n')
    mapping = {"api.py#public": [{"file": "docs/api.md", "section": "## API"}]}
    monkeypatch.setattr(docs, "doc_map", lambda _repo: mapping)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])
    assert result.exit_code == 0
    assert (repo / "docs/api.md").read_text() == '## API\nOriginal prose.\n'
    payload = json.loads(result.stdout)
    assert payload["candidates"][0]["api_change"] == "signature"


def test_current_source_aliases_are_unknown_instead_of_comparing_another_file(tmp_path):
    repo = _repo(tmp_path, {"api.py": 'def public(value=3): return value\n', "target.py": 'def public(value=4): return value\n'})
    (repo / "api.py").unlink()
    (repo / "api.py").symlink_to("target.py")
    rows = docs.worktree_candidates(str(repo), {})
    assert len(rows) == 1
    assert rows[0]["candidate"] == "api.py#*"
    assert "read safely" in rows[0]["reason"]


def test_committed_source_alias_is_not_treated_as_a_missing_api_baseline(tmp_path):
    repo = _repo(tmp_path, {"target.py": 'def public(value=3): return value\n'})
    path = repo / "api.py"
    path.symlink_to("target.py")
    for args in [["add", "api.py"], ["commit", "-qm", "source alias"]]:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    path.unlink()
    path.write_text('def public(value=4): return value\n')
    rows = docs.worktree_candidates(str(repo), {})
    assert len(rows) == 1
    assert rows[0]["candidate"] == "api.py#*"
    assert "read safely" in rows[0]["reason"]


@pytest.mark.parametrize("deleted", [False, True])
def test_added_and_deleted_mapped_json_files_compare_against_an_empty_surface(tmp_path, deleted):
    files = {"api.py": "def public(): pass\n"}
    if deleted:
        files["settings.json"] = '{"timeout": 3}'
    repo = _repo(tmp_path, files)
    path = repo / "settings.json"
    if deleted:
        path.unlink()
    else:
        path.write_text('{"timeout": 3}')
    target = [{"file": "docs/config.md", "section": "## Settings"}]
    rows = docs.worktree_candidates(str(repo), {"settings.json#timeout": target})
    assert len(rows) == 1
    assert rows[0]["candidate"] == "settings.json#timeout"
    assert rows[0]["api_change"] == ("removed" if deleted else "added")


@pytest.mark.parametrize("path", [".settings.json", "docs/examples/settings.json", "tests/fixtures/settings.toml"])
def test_explicit_config_mapping_overrides_prose_test_and_dot_filename_exclusions(tmp_path, path):
    before, after = ('timeout = 3\n', 'timeout = 4\n') if path.endswith(".toml") else ('{"timeout": 3}', '{"timeout": 4}')
    repo = _repo(tmp_path, {path: before})
    (repo / path).write_text(after)
    assert docs.worktree_candidates(str(repo), {}) == []
    target = [{"file": "docs/config.md", "section": "## Settings"}]
    rows = docs.worktree_candidates(str(repo), {f"{path}#timeout": target})
    assert len(rows) == 1
    assert rows[0]["candidate"] == f"{path}#timeout"
    assert rows[0]["confidence"] == "unknown"


@pytest.mark.parametrize("path", [".amu/internal.json", "node_modules/package/settings.json", ".venv/package/settings.toml", ".aws/settings.json", "credentials.json", ".env.json"])
def test_explicit_mapping_does_not_opt_internal_caches_or_credentials_into_review(tmp_path, path):
    before, after = ('timeout = 3\n', 'timeout = 4\n') if path.endswith(".toml") else ('{"timeout": 3}', '{"timeout": 4}')
    repo = _repo(tmp_path, {path: before})
    (repo / path).write_text(after)
    target = [{"file": "docs/config.md", "section": "## Settings"}]
    assert docs.worktree_candidates(str(repo), {f"{path}#timeout": target}) == []


def test_config_parent_mapping_scopes_descendants_without_unmapped_sibling_noise(tmp_path):
    repo = _repo(tmp_path, {"settings.json": '{"response":{"value":0},"internal":{"value":0}}'})
    (repo / "settings.json").write_text('{"response":{"value":"text"},"internal":{"value":1}}')
    target = [{"file": "docs/config.md", "section": "## Response"}]
    rows = docs.worktree_candidates(str(repo), {"settings.json#response": target})
    assert [row["candidate"] for row in rows] == ["settings.json#response.value"]
    assert rows[0]["targets"] == target


@pytest.mark.parametrize("mapped_leaf", [False, True])
def test_config_closest_parent_mapping_and_exact_leaf_take_precedence(tmp_path, mapped_leaf):
    repo = _repo(tmp_path, {"settings.json": '{"response":{"nested":{"value":0}}}'})
    (repo / "settings.json").write_text('{"response":{"nested":{"value":1}}}')
    outer = [{"file": "docs/config.md", "section": "## Outer"}]
    nearest = [{"file": "docs/config.md", "section": "## Nearest"}]
    leaf = [{"file": "docs/config.md", "section": "## Leaf"}]
    mapping = {"settings.json#response": outer, "settings.json#response.nested": nearest, "settings.json#*": outer}
    if mapped_leaf:
        mapping["settings.json#response.nested.value"] = leaf
    rows = docs.worktree_candidates(str(repo), mapping)
    assert len(rows) == 1
    assert rows[0]["targets"] == (leaf if mapped_leaf else nearest)


def test_config_literal_dotted_key_never_aliases_a_nested_parent_mapping(tmp_path):
    repo = _repo(tmp_path, {"settings.json": '{"a.b":{"value":0},"a":{"b":{"value":0}}}'})
    (repo / "settings.json").write_text('{"a.b":{"value":1},"a":{"b":{"value":1}}}')
    nested = [{"file": "docs/config.md", "section": "## Nested"}]
    literal = [{"file": "docs/config.md", "section": "## Literal"}]
    rows = docs.worktree_candidates(str(repo), {"settings.json#a": nested})
    assert [row["candidate"] for row in rows] == ["settings.json#a.b.value"]
    rows = docs.worktree_candidates(str(repo), {"settings.json#a": nested, r"settings.json#a\.b": literal})
    found = {row["candidate"]: row["targets"] for row in rows}
    assert found == {"settings.json#a.b.value": nested, r"settings.json#a\.b.value": literal}


def test_config_literal_backslashes_do_not_swallow_real_parent_separators(tmp_path):
    import json

    before = {"root\\": {"nested.key": {"value": 0}}, "root": {"value": 0}}
    after = {"root\\": {"nested.key": {"value": 1}}, "root": {"value": 1}}
    repo = _repo(tmp_path, {"settings.json": json.dumps(before)})
    (repo / "settings.json").write_text(json.dumps(after))
    target = [{"file": "docs/config.md", "section": "## Literal"}]
    rows = docs.worktree_candidates(str(repo), {r"settings.json#root\\.nested\.key": target})
    assert [row["candidate"] for row in rows] == [r"settings.json#root\\.nested\.key.value"]
    assert rows[0]["targets"] == target


def test_complete_manifest_docstring_only_edit_does_not_create_a_legacy_body_candidate(tmp_path):
    source = 'def public():\n    "before"\n    return 1\n'
    repo = _repo(tmp_path, {"api.py": source})
    (repo / "api.py").write_text(source.replace('"before"', '"after"'))
    assert docs.worktree_candidates(str(repo), {}) == []


def test_partial_manifest_retains_legacy_docstring_evidence_and_file_unknown(tmp_path):
    source = 'from .core import *\ndef public():\n    "before"\n    return 1\n'
    repo = _repo(tmp_path, {"api.py": source})
    (repo / "api.py").write_text(source.replace('"before"', '"after"'))
    found = {row["candidate"]: row for row in docs.worktree_candidates(str(repo), {})}
    assert found["api.py#public"]["class"] == "body"
    assert found["api.py#*"]["confidence"] == "unknown"
