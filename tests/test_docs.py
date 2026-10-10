import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from amu import cli, docs

DIFF = json.loads((Path(__file__).parent / "fixtures" / "diff.json").read_text())
DMAP = {"amu/classify.py#classify_consumers": [{"file": "docs/x.md", "section": "## classify"}]}


def test_signature_change_on_mapped_export_is_suggested_draft():
    c = next(x for x in docs.candidates(DIFF, DMAP) if x["candidate"].endswith("classify_consumers"))
    assert c["confidence"] == "sound" and docs.draft(c).startswith("SUGGESTED")


def test_body_change_is_unknown_candidate():
    c = next(x for x in docs.candidates(DIFF, DMAP) if x["candidate"].endswith("build_plan"))
    assert c["confidence"] == "unknown" and c["class"] == "body"


def test_unmapped_is_guessed_flag_only():
    c = next(x for x in docs.candidates(DIFF, DMAP) if x["candidate"].endswith("capabilities"))
    assert c["confidence"] == "guessed" and not c["targets"]


def test_committed_private_or_protocol_graph_changes_are_manual_unknowns_not_silently_dropped():
    diff = {
        "files": [
            {
                "path": "api.py",
                "changes": [
                    {"type": "signature_changed", "kind": "function", "name": "_exported"},
                    {"type": "signature_changed", "kind": "method", "name": "Widget.__init__"},
                ],
            }
        ]
    }

    found = {candidate["candidate"]: candidate for candidate in docs.candidates(diff, {})}

    assert found["api.py#_exported"]["confidence"] == "unknown"
    assert found["api.py#Widget.__init__"]["confidence"] == "unknown"
    assert all("requires review" in candidate["reason"] for candidate in found.values())


def test_auto_never_applies_unknown(tmp_path):
    for c in docs.candidates(DIFF, DMAP):
        if c["confidence"] != "sound":
            assert docs.apply(c, str(tmp_path)) is False


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _committed_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for relative, source in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "amu-docs-test@example.invalid")
    _git(repo, "config", "user.name", "AMU docs test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _sound_signature_diff(path: str = "api.py", name: str = "public") -> dict:
    return {
        "files": [
            {
                "path": path,
                "changes": [
                    {
                        "type": "signature_changed",
                        "kind": "function",
                        "name": name,
                        "old_signature": f"def {name}(value)",
                        "new_signature": f"def {name}(value, *, strict=False)",
                    }
                ],
            }
        ]
    }


def _write_doc_map(repo: Path, dmap: dict) -> None:
    (repo / ".amu").mkdir()
    (repo / ".amu" / "doc-map.json").write_text(json.dumps(dmap))


def test_doc_map_accepts_prd_entries_and_optional_anchor(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_doc_map(
        repo,
        {
            "version": 1,
            "entries": [
                {"symbol": "api.py#public", "doc_file": "docs/api.md", "anchor": "## API"},
                {"symbol": "api.py#whole_file", "doc_file": "docs/api.md"},
            ],
        },
    )

    assert docs.doc_map(str(repo)) == {
        "api.py#public": [{"file": "docs/api.md", "section": "## API"}],
        "api.py#whole_file": [{"file": "docs/api.md", "section": ""}],
    }


def test_worktree_candidates_cover_add_remove_signature_and_body_changes(tmp_path):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": """\
def signature(value):
    return value


def removed():
    return 1


class Widget:
    def render(self, enabled=False):
        return enabled
""",
            "old_api.py": "def removed_file_export():\n    return 1\n",
        },
    )
    (repo / "api.py").write_text(
        """\
def signature(value, *, verbose=False):
    return value


def added():
    return 2


class Widget:
    def render(self, enabled=False):
        return not enabled
"""
    )
    (repo / "old_api.py").unlink()
    (repo / "new_api.py").write_text("def added_file_export():\n    return 3\n")
    dmap = {"api.py#signature": [{"file": "docs/api.md", "section": "## API"}]}

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), dmap)}

    assert found["api.py#signature"]["class"] == "signature"
    assert found["api.py#removed"]["class"] == "remove"
    assert found["api.py#added"]["class"] == "add_export"
    assert found["api.py#Widget.render"]["class"] == "body"
    assert found["old_api.py#removed_file_export"]["class"] == "remove"
    assert found["new_api.py#added_file_export"]["class"] == "add_export"
    assert all(candidate["confidence"] == "unknown" for candidate in found.values())
    assert found["api.py#signature"]["targets"] == dmap["api.py#signature"]


def test_worktree_malformed_and_unsupported_source_are_explicit_unknowns(tmp_path):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public():\n    return 1\n",
            "web.ts": "export const api = () => 1;\n",
            "api.jl": "public() = 1\n",
            "poetry.lock": "metadata-version = '2.0'\n",
        },
    )
    (repo / "api.py").write_text("def public(:\n    return 2\n")
    (repo / "web.ts").write_text("export const api = () => 2;\n")
    (repo / "api.jl").write_text("public() = 2\n")
    (repo / "poetry.lock").write_text("metadata-version = '2.1'\n")

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    assert found["api.py#*"]["confidence"] == "unknown"
    assert "parse failed" in found["api.py#*"]["reason"]
    assert found["web.ts#*"]["confidence"] == "unknown"
    assert "unsupported" in found["web.ts#*"]["reason"]
    assert found["api.jl#*"]["confidence"] == "unknown"
    assert "unsupported" in found["api.jl#*"]["reason"]
    assert "poetry.lock#*" not in found


def test_all_candidates_preserves_graph_diff_candidates_before_worktree_evidence(tmp_path):
    repo = _committed_repo(tmp_path, {"api.py": "def public(value):\n    return value\n"})
    (repo / "api.py").write_text("def public(value, *, strict=False):\n    return value\n")

    graph_candidates = docs.candidates(DIFF, {})
    combined = docs.all_candidates(DIFF, str(repo), {})

    assert [candidate["candidate"] for candidate in combined[: len(graph_candidates)]] == [
        candidate["candidate"] for candidate in graph_candidates
    ]
    worktree = next(candidate for candidate in combined if candidate["candidate"] == "api.py#public")
    assert worktree["class"] == "signature"
    assert worktree["confidence"] == "unknown"


def test_worktree_module_level_reexport_change_is_not_silently_ignored(tmp_path):
    repo = _committed_repo(tmp_path, {"package/__init__.py": "from .api import public\n"})
    (repo / "package/__init__.py").write_text("from .api import replacement\n")

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    candidate = found["package/__init__.py#*"]
    assert candidate["confidence"] == "unknown"
    assert "module-level" in candidate["reason"]


def test_worktree_dynamic_module_api_hook_is_detected(tmp_path):
    repo = _committed_repo(tmp_path, {"api.py": "def __getattr__(name):\n    return name\n"})
    (repo / "api.py").write_text("def __getattr__(name):\n    return name.upper()\n")

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    assert found["api.py#__getattr__"]["class"] == "body"
    assert found["api.py#__getattr__"]["confidence"] == "unknown"


def test_worktree_candidates_include_private_all_exports_and_protocol_dunders(tmp_path):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": """\
__all__ = ["_exported", "_Widget"]


def _exported(value):
    return value


class _Widget:
    def __call__(self, value):
        return value
""",
        },
    )
    (repo / "api.py").write_text(
        """\
__all__ = ["_exported", "_Widget"]


def _exported(value, *, strict=False):
    return value


class _Widget:
    def __call__(self, value, *, strict=False):
        return value
"""
    )

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    assert found["api.py#_exported"]["class"] == "signature"
    assert found["api.py#_Widget.__call__"]["class"] == "signature"
    assert all(candidate["confidence"] == "unknown" for candidate in found.values())


def test_dynamic_all_export_definition_is_a_file_level_unknown(tmp_path):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": """\
__all__ = exported_names()


def _exported(value):
    return value
""",
        },
    )
    (repo / "api.py").write_text(
        """\
__all__ = exported_names()


def _exported(value, *, strict=False):
    return value
"""
    )

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    assert found["api.py#*"]["confidence"] == "unknown"
    assert "__all__" in found["api.py#*"]["reason"]


def test_global_worktree_enumeration_unknown_blocks_automatic_docs_apply():
    graph_candidate = {"candidate": "api.py#public", "confidence": "sound"}
    unavailable_worktree = {"candidate": "<worktree>#*", "evidence": "worktree-source"}

    block = docs.worktree_auto_block(graph_candidate, [graph_candidate, unavailable_worktree])

    assert block is not None
    assert block["worktree_candidate"] == "<worktree>#*"
    assert "could not be enumerated" in block["reason"]


def test_worktree_scan_failure_is_an_explicit_unknown_not_an_empty_result(tmp_path):
    plain = tmp_path / "not-a-repository"
    plain.mkdir()

    candidates = docs.worktree_candidates(str(plain), {})

    assert candidates[0]["candidate"] == "<worktree>#*"
    assert candidates[0]["confidence"] == "unknown"
    assert "worktree" in candidates[0]["reason"].lower() or "git" in candidates[0]["reason"].lower()


def test_importable_test_helpers_are_not_silently_excluded_from_worktree_api_review(tmp_path):
    repo = _committed_repo(tmp_path, {"src/test_helpers.py": "def public_helper(value):\n    return value\n"})
    source = repo / "src" / "test_helpers.py"
    source.write_text("def public_helper(value, *, strict=False):\n    return value\n")

    found = {candidate["candidate"]: candidate for candidate in docs.worktree_candidates(str(repo), {})}

    assert found["src/test_helpers.py#public_helper"]["class"] == "signature"


def test_docs_auto_never_writes_from_a_worktree_only_candidate(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nExisting docs.\n",
        },
    )
    (repo / "api.py").write_text("def public(value, *, strict=False):\n    return value\n")
    dmap = {"api.py#public": [{"file": "docs/api.md", "section": "## public"}]}
    _write_doc_map(repo, dmap)
    original = (repo / "docs/api.md").read_text()
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})

    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["applied"] == []
    assert payload["candidates"][0]["candidate"] == "api.py#public"
    assert payload["candidates"][0]["consistent_with_diff"] is False
    assert (repo / "docs/api.md").read_text() == original


def test_docs_auto_blocks_a_sound_graph_candidate_shadowed_by_an_exact_worktree_change(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nExisting docs.\n",
        },
    )
    (repo / "api.py").write_text("def public(value, *, strict=False):\n    return value\n")
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "## public"}]})
    original = (repo / "docs/api.md").read_text()
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())

    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["applied"] == []
    assert payload["candidates"][0]["candidate"] == "api.py#public"  # retained for manual --apply review
    assert len(payload["worktree_blocks"]) == 1
    block = payload["worktree_blocks"][0]
    assert block["id"] == "d1"
    assert block["candidate"] == "api.py#public"
    assert block["worktree_candidate"] == "api.py#public"
    assert "exact-target" in block["reason"]
    assert payload["not_applied"][0]["blocked_by_worktree"] == "api.py#public"
    assert "shadowed candidates" in payload["next"]
    assert (repo / "docs/api.md").read_text() == original


def test_docs_auto_blocks_a_sound_graph_candidate_when_file_level_worktree_evidence_is_unknown(
    fake_entire, tmp_path, monkeypatch
):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nExisting docs.\n",
        },
    )
    # The AST cannot safely name an edited symbol, so it creates api.py#*.
    (repo / "api.py").write_text("def public(:\n    return value\n")
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "## public"}]})
    original = (repo / "docs/api.md").read_text()
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())

    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["applied"] == []
    assert payload["candidates"][0]["candidate"] == "api.py#public"
    assert payload["worktree_blocks"][0]["worktree_candidate"] == "api.py#*"
    assert "file-level" in payload["worktree_blocks"][0]["reason"]
    assert payload["not_applied"][0]["blocked_by_worktree"] == "api.py#*"
    assert (repo / "docs/api.md").read_text() == original


def test_docs_filters_malformed_doc_map_entries_without_losing_a_safe_target(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nExisting docs.\n",
        },
    )
    _write_doc_map(
        repo,
        {
            "api.py#public": [
                "not a mapping",
                {"file": "docs/api.md"},
                {"section": "## public"},
                {"file": 3, "section": "## public"},
                {"file": "docs/api.md", "section": "## public"},
            ]
        },
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())

    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["applied"] == ["d1"]
    assert "`api.py#public` — signature" in (repo / "docs/api.md").read_text()


def test_docs_treats_a_non_mapping_doc_map_as_unmapped_instead_of_crashing(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nExisting docs.\n",
        },
    )
    _write_doc_map(repo, ["not a mapping"])
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())

    result = CliRunner().invoke(cli.app, ["docs", "--mode", "auto", "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["applied"] == []
    assert payload["candidates"][0]["doc_file"] is None
    assert "SUGGESTED" not in (repo / "docs/api.md").read_text()


def _two_signature_changes(reverse: bool = False) -> dict:
    changes = [
        {
            "type": "signature_changed",
            "kind": "function",
            "name": "first",
            "old_signature": "def first(value)",
            "new_signature": "def first(value, *, strict=False)",
        },
        {
            "type": "signature_changed",
            "kind": "function",
            "name": "second",
            "old_signature": "def second(value)",
            "new_signature": "def second(value, *, strict=False)",
        },
    ]
    return {"files": [{"path": "api.py", "changes": list(reversed(changes)) if reverse else changes}]}


def test_docs_apply_refuses_a_stale_display_id_after_candidates_reorder(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def first(value):\n    return value\n\n\ndef second(value):\n    return value\n",
            "docs/first.md": "# First\n\n## first\nOriginal first docs.\n",
            "docs/second.md": "# Second\n\n## second\nOriginal second docs.\n",
        },
    )
    _write_doc_map(
        repo,
        {
            "api.py#first": [{"file": "docs/first.md", "section": "## first"}],
            "api.py#second": [{"file": "docs/second.md", "section": "## second"}],
        },
    )
    diffs = iter([_two_signature_changes(), _two_signature_changes(reverse=True)])
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: next(diffs))

    listed = CliRunner().invoke(cli.app, ["docs", "--mode", "draft", "--json", "--repo", str(repo)])
    applied = CliRunner().invoke(cli.app, ["docs", "--apply", "d1", "--json", "--repo", str(repo)])

    listing = json.loads(listed.stdout)
    payload = json.loads(applied.stdout)
    assert listed.exit_code == 0
    assert listing["candidates"][0]["candidate"] == "api.py#first"
    assert applied.exit_code == 0
    assert payload["applied"] == []
    assert "stale or reordered" in payload["not_applied"][0]["why"]
    assert "SUGGESTED" not in (repo / "docs/first.md").read_text()
    assert "SUGGESTED" not in (repo / "docs/second.md").read_text()


def test_docs_apply_accepts_an_issued_id_when_its_candidate_is_unchanged(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(
        tmp_path,
        {
            "api.py": "def public(value):\n    return value\n",
            "docs/api.md": "# API\n\n## public\nOriginal docs.\n",
        },
    )
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "## public"}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())

    listed = CliRunner().invoke(cli.app, ["docs", "--mode", "draft", "--json", "--repo", str(repo)])
    applied = CliRunner().invoke(cli.app, ["docs", "--apply", "d1", "--json", "--repo", str(repo)])

    listing = json.loads(listed.stdout)
    payload = json.loads(applied.stdout)
    assert listed.exit_code == 0
    assert listing["candidates"][0]["id"] == "d1"
    assert listing["candidates"][0]["fingerprint"]
    assert applied.exit_code == 0
    assert payload["applied"] == ["d1"]
    assert "`api.py#public` — signature" in (repo / "docs/api.md").read_text()


def _mapped_candidate(filename: str) -> dict:
    return {
        "candidate": "amu/docs.py#apply",
        "class": "signature",
        "old": "apply(c)",
        "new": "apply(c, repo)",
        "targets": [{"file": filename, "section": "## API"}],
    }


def _write_mapped_doc(repo: Path) -> Path:
    doc = repo / "docs" / "guide.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Guide\n\n## API\nExisting text.\n")
    return doc


def test_apply_updates_an_in_repo_doc_once(tmp_path):
    doc = _write_mapped_doc(tmp_path)
    candidate = _mapped_candidate("docs/guide.md")
    addition = docs.draft(candidate).replace("SUGGESTED: ", "")

    assert docs.apply(candidate, str(tmp_path)) is True
    assert docs.apply(candidate, str(tmp_path)) is False
    assert doc.read_text().count(addition) == 1


def test_apply_rejects_traversal_target_even_when_it_resolves_inside_repo(tmp_path):
    doc = _write_mapped_doc(tmp_path)
    candidate = _mapped_candidate("docs/../docs/guide.md")

    assert docs.apply(candidate, str(tmp_path)) is False
    assert "SUGGESTED" not in doc.read_text()


def test_apply_rejects_absolute_target_even_when_it_is_inside_repo(tmp_path):
    doc = _write_mapped_doc(tmp_path)
    candidate = _mapped_candidate(str(doc.resolve()))

    assert docs.apply(candidate, str(tmp_path)) is False
    assert "SUGGESTED" not in doc.read_text()


def test_apply_never_uses_a_doc_map_entry_to_write_source_code(tmp_path):
    source = tmp_path / "amu" / "module.py"
    source.parent.mkdir()
    source.write_text("## API\nDo not edit.\n")
    candidate = _mapped_candidate("amu/module.py")

    assert docs.apply(candidate, str(tmp_path)) is False
    assert source.read_text() == "## API\nDo not edit.\n"


def test_apply_rejects_symlink_target_that_escapes_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\n## API\nDo not change.\n")
    link = repo / "docs" / "escape.md"
    link.parent.mkdir()
    link.symlink_to(outside)
    candidate = _mapped_candidate("docs/escape.md")

    assert docs.apply(candidate, str(repo)) is False
    assert outside.read_text() == "# Outside\n\n## API\nDo not change.\n"
