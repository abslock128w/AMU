from pathlib import Path

import pytest

from amu import docgraph, docreview, docs, state


def candidate(file, section):
    return {"id": "d1", "candidate": "api.py#f", "class": "body", "confidence": "unknown",
            "targets": [{"file": file, "section": section}]}


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_code_comments_are_not_headings(fence):
    text = f"# API\n\n{fence}python\n# example comment\n{fence}\n\n## Next\nKeep\n"
    assert [s["title"] for s in docgraph._sections("docs/api.md", text)] == ["API", "Next"]


def test_rst_levels_follow_first_use_not_character_order():
    text = "Parent\n******\n\nIntro\n\nChild\n=====\n\nBody\n\nSibling\n*******\n\nKeep\n"
    rows = docgraph._sections("docs/api.rst", text)
    assert rows[0]["end_line"] == 10
    assert [s["title"] for s in rows] == ["Parent", "Child", "Sibling"]


@pytest.mark.parametrize("suffix,heading,tail", [
    ("md", "API\n===", "## Child\n\nKeep nested.\n\n# Next\nKeep sibling.\n"),
    ("rst", "*****\nAPI\n*****", ".. _next-label:\n\nNext\n****\nKeep sibling.\n"),
])
def test_review_preserves_exact_prefix_children_and_next_labels(tmp_path, suffix, heading, tail):
    (tmp_path / "docs").mkdir()
    file = f"docs/api.{suffix}"
    path = tmp_path / file
    prefix = "Preamble.\r\n\r\n" + heading.replace("\n", "\r\n") + "\r\n"
    tail = tail.replace("\n", "\r\n")
    path.write_bytes((prefix + "\r\nOld.\r\n\r\n" + tail).encode())
    c = candidate(file, "API")
    prepared = docreview.prepare(c, str(tmp_path))
    assert "Keep" not in Path(prepared["file"]).read_text()
    Path(prepared["file"]).write_text("New documentation.\n")
    docreview.apply(c, str(tmp_path))
    result = path.read_bytes()
    assert result.startswith(prefix.encode())
    assert result.endswith(tail.encode())


def test_duplicate_heading_is_not_a_unique_edit_target(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    original = "# API\nOne\n\n# API\nTwo\n"
    path.write_text(original)
    c = candidate("docs/api.md", "# API")
    with pytest.raises(ValueError, match="ambiguous|unique"):
        docreview.prepare(c, str(tmp_path))
    assert docs.apply(c, str(tmp_path)) is False
    assert path.read_text() == original


def test_rst_directive_contents_are_not_headings():
    text = "API\n===\n\n.. code-block:: text\n\n    Fake\n    ====\n\nNext\n====\n"
    assert [s["title"] for s in docgraph._sections("docs/api.rst", text)] == ["API", "Next"]


@pytest.mark.parametrize("body", [
    "    # indented code\n", "> # quoted heading\n", "- item\n\n  # nested heading\n",
    "<!--\n# comment heading\n-->\n", "<pre>\n# raw heading\n</pre>\n",
])
def test_markdown_containers_do_not_create_editable_sections(body):
    rows = docgraph._sections("docs/api.md", "# API\n\n" + body + "\n# Next\n")
    assert [s["title"] for s in rows] == ["API", "Next"]


def test_fenced_fake_mapping_cannot_be_applied_or_seeded(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    original = "# API\n\n```python\n# `fake`\n```\n"
    path.write_text(original)
    assert "fake" not in docs.draft_doc_map(str(tmp_path))
    assert not docs.apply(candidate("docs/api.md", "# `fake`"), str(tmp_path))
    assert path.read_text() == original


def test_old_draft_without_parser_boundaries_is_rejected(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    path.write_text("# API\n\nOriginal\n\n## Child\nKeep\n")
    original = path.read_bytes()
    c = candidate("docs/api.md", "# API")
    draft = docreview.prepare(c, str(tmp_path))
    Path(draft["file"]).write_text("Updated\n")
    name = f"doc-reviews/{docs.candidate_fingerprint(c)}.json"
    metadata = state.read_json(name, str(tmp_path))
    metadata["schema"] = 1
    metadata.pop("region")
    state.write_json(name, metadata, str(tmp_path))
    with pytest.raises(ValueError, match="boundaries changed"):
        docreview.apply(c, str(tmp_path))
    assert path.read_bytes() == original


def test_mdx_expression_is_manual_not_guessed_edit(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/api.mdx").write_text("# API\n\n<Component>\n\n# Inside\n\n</Component>\n")
    with pytest.raises(ValueError, match="MDX"):
        docreview.prepare(candidate("docs/api.mdx", "API"), str(tmp_path))


def test_generated_section_ids_never_collide():
    rows = docgraph._sections("docs/api.md", "# API\n\n# API\n\n# API-2\n")
    assert len({s["id"] for s in rows}) == 3


def test_crlf_unchanged_draft_is_not_treated_as_an_edit(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    original = b"# API\r\n\r\nOriginal\r\n"
    path.write_bytes(original)
    c = candidate("docs/api.md", "API")
    docreview.prepare(c, str(tmp_path))
    with pytest.raises(ValueError, match="unchanged"):
        docreview.apply(c, str(tmp_path))
    assert path.read_bytes() == original


def test_heading_without_final_newline_is_preserved(tmp_path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "docs/api.md"
    path.write_text("# API")
    c = candidate("docs/api.md", "API")
    prepared = docreview.prepare(c, str(tmp_path))
    Path(prepared["file"]).write_text("New body.")
    docreview.apply(c, str(tmp_path))
    assert path.read_text().startswith("# API\nNew body.")


def test_malformed_rst_adornment_refuses_edit(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/api.rst").write_text("API\n===\n\nMuch longer title\n---\nBody\n")
    with pytest.raises(ValueError, match="malformed RST"):
        docreview.prepare(candidate("docs/api.rst", "API"), str(tmp_path))
