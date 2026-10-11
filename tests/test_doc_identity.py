from amu import docgraph


def test_missing_fragment_is_unresolved_and_local_anchor_works(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("# API\n\n[bad](api.md#missing) [local](#usage)\n\n## Usage\n\nUse it.\n")
    graph = docgraph.build(str(tmp_path))
    links = [e for e in graph["edges"] if e["relation"] == "LINKS_TO"]
    assert [(e["source"], e["target"]) for e in links] == [("doc:docs/api.md#api", "doc:docs/api.md#usage")]
    assert any("missing" in warning for warning in graph["warnings"])


def test_same_leaf_in_another_module_does_not_satisfy_doc_relation():
    graph = {"edges": [{"relation": "MENTIONS", "source": "doc:x#y", "target": "a.py#read"}]}
    assert not docgraph._section_mentions(graph, {"doc:x#y"}, "b.py#read")


def test_ambiguous_short_mention_does_not_resolve_to_both_modules(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("# API\n\nCall read().\n")
    mapping = {"a.py#read": [{"file": "docs/api.md", "section": "# API"}],
               "b.py#read": [{"file": "docs/api.md", "section": "# API"}]}
    graph = docgraph.build(str(tmp_path), doc_map=mapping)
    assert not [e for e in graph["edges"] if e["relation"] == "MENTIONS"]
    assert any("ambiguous" in warning for warning in graph["warnings"])


def test_exact_reference_does_not_create_a_short_name_edge_to_another_module(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("# API\n\nCall `a.py#read`.\n")
    graph = docgraph.build(str(tmp_path), doc_map={"b.py#read": [{"file": "docs/api.md", "section": "# API"}]})
    mentions = [e["target"] for e in graph["edges"] if e["relation"] == "MENTIONS"]
    assert mentions == ["a.py#read"]
