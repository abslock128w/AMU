from amu.contract import check_contract, parse_graph_diff

CONTRACT = {"targets": [{"symbol": "amu/classify.py#classify_consumers", "change_class": "signature"}], "base_sha": "x", "map_hash": "h",
            "phases": [{"n": 1, "kind": "additive", "nodes": [], "files": ["amu/classify.py"], "tests": ["tests/test_classify.py"]},
                       {"n": 2, "kind": "leaves", "nodes": ["n1"], "files": ["tests/test_classify.py"], "tests": ["tests/test_classify.py"]},
                       {"n": 3, "kind": "interior", "nodes": ["n2"], "files": ["amu/cli.py"], "tests": []},
                       {"n": 4, "kind": "removal", "nodes": [], "files": ["amu/classify.py"], "tests": []}],
            "frozen_signatures": ["amu/cli.py#brief"], "allowed_files": ["amu/classify.py", "tests/test_classify.py", "amu/cli.py"], "created_at": "now"}


def test_phase_drift_when_later_phase_file_changes():
    r = check_contract(CONTRACT, [], ["amu/cli.py"], phase=1)
    assert not r.passed and r.violations[0].kind.value == "PHASE_DRIFT"


def test_frozen_signature_blocks():
    ch = parse_graph_diff({"files": [{"path": "amu/cli.py", "changes": [{"type": "signature_changed", "kind": "function", "name": "brief"}]}]})
    r = check_contract(CONTRACT, ch, ["amu/cli.py"], phase=3)
    assert any(v.kind.value == "FROZEN_SIGNATURE" for v in r.violations) and not r.passed


def test_unknown_touched_blocks_until_a_human_resolves_the_graph_gap():
    r = check_contract(CONTRACT, [], ["amu/classify.py"], phase=1, unknown_files={"amu/classify.py"})
    assert not r.passed
    assert r.violations[0].kind.value == "UNKNOWN_TOUCHED"
    assert r.violations[0].severity.value == "ERROR"


def test_out_of_scope_blocks():
    r = check_contract(CONTRACT, [], ["amu/other.py"], phase=1)
    assert not r.passed and r.violations[0].kind.value == "OUT_OF_SCOPE"


def test_real_diff_shape_parses():
    ch = parse_graph_diff({"files": [{"path": "a.py", "changes": [{"type": "removed", "kind": "function", "name": "f", "dependents_count": 2}]}]})
    assert ch[0]["change"] == "removed" and ch[0]["dependents_count"] == 2


def test_default_file_wildcard_allows_a_declared_removal():
    contract = {**CONTRACT, "targets": [{"symbol": "amu/classify.py#*", "change_class": "remove"}]}
    changes = parse_graph_diff({"files": [{"path": "amu/classify.py", "changes": [{"type": "removed", "kind": "function", "name": "classify_consumers"}]}]})

    result = check_contract(contract, changes, ["amu/classify.py"], phase=4)

    assert not any(v.kind.value == "UNDECLARED_REMOVAL" for v in result.violations)


def test_unguarded_change_requires_tests_for_its_own_phase():
    changes = parse_graph_diff({"files": [{"path": "amu/cli.py", "changes": [{"type": "body_changed", "kind": "function", "name": "check"}]}]})

    result = check_contract(CONTRACT, changes, ["amu/cli.py"], phase=3)

    assert any(v.kind.value == "UNGUARDED_CHANGE" for v in result.violations)
