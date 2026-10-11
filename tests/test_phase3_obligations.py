"""Expression-level check targets remain source backed and bounded."""

from amu.change_analysis import analyze_python_change


def _obligations(before, after):
    return analyze_python_change("api.py", before, after)["obligations"]


def test_boundary_and_return_produce_distinct_check_targets():
    before = "def check(value):\n    if value < 0:\n        return None\n    return value\n"
    after = "def check(value):\n    if value <= 0:\n        return []\n    return value\n"
    rows = _obligations(before, after)
    assert [row["category"] for row in rows] == ["branch", "return"]
    assert rows[0]["condition"]["target_value"] == 0
    assert rows[0]["before"][0]["location"]["line"] == 2
    assert rows[0]["after"][0]["location"]["line"] == 2
    assert rows[1]["changed_operation"] == {"before": "return None", "after": "return []"}
    assert all(row["execution_status"] == "not-run" for row in rows)


def test_formatting_and_unrelated_expression_do_not_invent_boundary():
    before = "def check(value):\n    if value < 0:\n        return value\n"
    assert _obligations(before, "def check(value):\n\n    if value < 0: # same condition\n        return value\n") == []
    changed = _obligations(before, "def check(value):\n    if value < 1:\n        return value\n")
    assert changed[0]["condition"]["target_value"] is None


def test_handler_return_names_failure_path_without_claiming_regression():
    before = "def read():\n    try:\n        work()\n    except ValueError:\n        return None\n"
    after = before.replace("return None", "return -1")
    row = next(row for row in _obligations(before, after) if row["category"] == "return")
    assert row["condition"] == {"kind": "exception-handler", "exception": "ValueError"}
    assert "regression" in row["limits"][0]


def test_simple_default_tracks_omitted_argument():
    rows = _obligations("def load(limit=10):\n    return limit\n",
                        "def load(limit=20):\n    return limit\n")
    assert len(rows) == 1
    assert rows[0]["condition"] == {"kind": "omitted-argument", "parameter": "limit"}
    assert rows[0]["observable"]["kind"] == "argument-binding"


def test_plain_self_class_state_resolves_but_instance_shadow_abstains():
    before = "class Queue:\n    capacity = 2\n    def ready(self):\n        return self.capacity > 0\n    def unrelated(self):\n        return True\n"
    after = before.replace("capacity = 2", "capacity = None")
    analysis = analyze_python_change("api.py", before, after)
    consumers = {row["consumer_id"] for row in analysis["state_dependencies"]}
    assert consumers == {"api.py#Queue.ready"}

    shadow = before.replace("    def ready(self):", "    def reset(self):\n        self.capacity = 1\n    def ready(self):")
    shadow_analysis = analyze_python_change("api.py", shadow, shadow.replace("capacity = 2", "capacity = None"))
    assert not any(row["consumer_id"] == "api.py#Queue.ready" for row in shadow_analysis["state_dependencies"])
    assert any("shadow" in row["reason"] for row in shadow_analysis["uncertainties"])


def test_secret_literal_condition_is_not_reintroduced_in_structured_fields():
    import json

    token = "sk-syntheticReviewValue12345"
    before = f"def check(value):\n    if value == '{token}':\n        return 1\n    return 0\n"
    rows = _obligations(before, before.replace(" == ", " != "))
    assert rows[0]["condition"] is None
    assert token not in json.dumps(rows)


def test_returned_comparison_has_equality_obligation():
    before = "def accepts(score, minimum):\n    return score > minimum\n"
    rows = _obligations(before, before.replace(" > ", " >= "))
    row = next(row for row in rows if row["category"] == "branch")
    assert row["condition"]["target_parameter"] == "minimum"
    assert row["observable"]["kind"] == "predicate-result"


def test_type_subscripts_do_not_become_mapping_key_obligations():
    before = "def calls(value: set[str]) -> set[str]:\n    result: set[str] = set()\n    return result\n"
    after = before.replace("set[str]", "set[int]")
    assert not any(row["category"] in {"keyed_access", "state_access"}
                   for row in _obligations(before, after))


def test_new_class_state_does_not_remove_an_unchanged_method_key():
    before = "class Sessions:\n    def __init__(self):\n        self.items = {}\n    def put(self, key, value):\n        self.items[key] = value\n"
    after = "class Sessions:\n    items = {}\n    def put(self, key, value):\n        self.items[key] = value\n"
    analysis = analyze_python_change("api.py", before, after)
    assert not any(row["category"] == "keyed_access"
                   for section in ("findings", "obligations") for row in analysis[section])
    assert any(row["category"] == "state_declaration" for row in analysis["obligations"])


def test_unresolved_receiver_still_has_source_backed_key_obligation():
    before = "class Catalog:\n    def get(self, tenant, item):\n        key = item\n        return self.cache[key]\n"
    after = before.replace("key = item", "key = (tenant, item)")
    rows = _obligations(before, after)
    row = next(row for row in rows if row["category"] == "keyed_access")
    assert row["changed_operation"] == {"before": "key = item", "after": "key = (tenant, item)"}
    assert row["observable"]["kind"] == "key-or-index-result"
    assert row["before"][0]["role"] == "key_definition"
