"""Only AMU executes; fixture and test sources below are parsed, never collected."""

import difflib
from dataclasses import replace

import pytest

from amu import value_flow
from amu.services import impact
from tests.test_integrated_pipeline import flow

SOURCE = 'class API:\n    def result(self):\n        return {"value": 1, "other": 9}\n'
FIXTURE = "import pytest\nfrom api import API\n@pytest.fixture\ndef api():\n    return API()\n"
TEST = 'import pytest\n@pytest.mark.parametrize("expected", [pytest.param(1, id="one")])\ndef test_value(api, expected):\n    assert api.result()["value"] == expected\n    assert 9 == 9\n'


def analyze(files):
    return flow(files, {**files, "api.py": files["api.py"].replace('"value": 1', '"value": 2')}, "api.py#API.result")


def test_conftest_receiver_reaches_parametrized_baseline_assertion():
    result = analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": TEST})
    assert {a["test_id"] for a in result["assertions"]} == {"tests/test_api.py::test_value"}
    call = next(s for s in result["assertions"][0]["path"] if s["role"] == "resolved_call")
    assert call["receiver"]["source_path"][-1]["role"] == "fixture_binding"
    assert call["receiver"]["source_path"][-1]["fixture_id"] == "tests/conftest.py#api"
    assert result["assertions"][0]["coverage_proven"] is False


def test_dependencies_and_source_factory_preserve_receiver():
    fixture = FIXTURE.replace(
        "def api():\n    return API()",
        "def base():\n    return make_api()\n@pytest.fixture\ndef api(base):\n    return base",
    ).replace("import API", "import make_api")
    source = SOURCE + "def make_api():\n    return API()\n"
    result = analyze({"api.py": source, "tests/conftest.py": fixture, "tests/test_api.py": TEST})
    assert result["assertions"]
    call = next(s for s in result["assertions"][0]["path"] if s["role"] == "resolved_call")
    assert [s["fixture_id"] for s in call["receiver"]["source_path"] if s["role"] == "fixture_binding"] == [
        "tests/conftest.py#base",
        "tests/conftest.py#api",
    ]


@pytest.mark.parametrize(
    "suffix",
    [
        '\n@pytest.fixture\ndef api():\n    return "other"\n',
        "\n@pytest.fixture(params=[1, 2])\ndef api(request):\n    return API()\n",
        "\n@pytest.fixture\ndef api():\n    yield API()\n",
        "\n@pytest.fixture\ndef api():\n    return api()\n",
    ],
)
def test_nearest_override_blocks_outer_receiver(suffix):
    result = analyze(
        {"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": "from api import API\n" + TEST + suffix}
    )
    assert not result["assertions"]


@pytest.mark.parametrize(
    "extra",
    [
        '\n@pytest.fixture(autouse=True)\ndef patcher(monkeypatch):\n    monkeypatch.setattr(API, "result", lambda self: 9)\n',
        '\npytest_plugins = ["custom"]\n',
        '\npytestmark = pytest.mark.usefixtures("patcher")\n',
        "\ndef pytest_collection_modifyitems(items):\n    opaque(items)\n",
    ],
)
def test_implicit_setup_or_plugins_do_not_promote_fixture_receiver(extra):
    assert not analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE + extra, "tests/test_api.py": TEST})[
        "assertions"
    ]


def test_parametrize_overrides_fixture_and_other_requested_fixture_may_mutate():
    for test in [
        TEST.replace('"expected", [pytest.param(1, id="one")]', '"api", [None]'),
        TEST.replace("api, expected", "api, expected, monkeypatch"),
    ]:
        assert not analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": test})["assertions"]


def test_unrelated_fields_reassignment_and_escape_remain_unpromoted():
    for test in [
        TEST.replace('["value"]', '["other"]'),
        TEST.replace("    assert api.result()", "    api = unknown()\n    assert api.result()"),
        TEST.replace("    assert api.result()", "    opaque(api)\n    assert api.result()"),
    ]:
        assert not analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": test})["assertions"]


def test_fixture_dependency_cycle_and_mixed_factory_abstain():
    for fixture in [
        FIXTURE.replace(
            "def api():\n    return API()",
            "def api(base):\n    return base\n@pytest.fixture\ndef base(api):\n    return api",
        ),
        FIXTURE.replace("def api():\n    return API()", "def api():\n    return factory(unknown())").replace(
            "import API", "import factory"
        ),
    ]:
        source = SOURCE + 'def factory(flag):\n    if flag:\n        return API()\n    return "other"\n'
        assert not analyze({"api.py": source, "tests/conftest.py": fixture, "tests/test_api.py": TEST})["assertions"]


def test_fixture_scope_cannot_use_sibling_conftest():
    assert not analyze({"api.py": SOURCE, "other/conftest.py": FIXTURE, "tests/test_api.py": TEST})["assertions"]


def test_imported_fixture_override_does_not_fall_through_to_conftest():
    test = 'from plugin import api\n' + TEST
    assert not analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": test})["assertions"]


@pytest.mark.parametrize("signature", ["api=None", "*, api=None"])
def test_default_test_argument_is_not_a_requested_fixture(signature):
    test = f'def test_value({signature}):\n    assert api.result()["value"] == 1\n'
    assert not analyze({"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": test})["assertions"]


def test_fixture_property_stored_field_and_unrelated_assertion():
    source = "class API:\n    def __init__(self):\n        self.value=1\n        self.other=9\n    @property\n    def result(self):\n        return self.value\n"
    test = "def test_value(api):\n    assert api.result == 1\n    assert api.other == 9\n"
    files = {"api.py": source, "tests/conftest.py": FIXTURE, "tests/test_api.py": test}
    result = flow(files, {**files, "api.py": source.replace("self.value=1", "self.value=2")}, "api.py#API.__init__")
    assert len(result["assertions"]) == 1
    assert result["assertions"][0]["assertion"]["location"]["line"] == 2
    assert any(s["role"] == "state_write" for s in result["assertions"][0]["path"])


@pytest.mark.parametrize(
    "prefix",
    [
        "    def __init__(self):\n        raise ValueError()\n",
        "    def __init__(self):\n        opaque(self)\n",
        "    def __init__(self):\n        unsupported()\n",
    ],
)
def test_fixture_initializer_failures_and_unknown_effects_abstain(prefix):
    assert not analyze(
        {
            "api.py": SOURCE.replace("class API:\n", "class API:\n" + prefix),
            "tests/conftest.py": FIXTURE,
            "tests/test_api.py": TEST,
        }
    )["assertions"]


def test_same_fixture_allocation_is_shared_for_escape_invalidation():
    fixture = FIXTURE + "@pytest.fixture\ndef alias(api):\n    return api\n"
    test = TEST.replace("api, expected", "api, alias, expected").replace(
        "    assert api.result()", "    opaque(alias)\n    assert api.result()"
    )
    assert not analyze({"api.py": SOURCE, "tests/conftest.py": fixture, "tests/test_api.py": test})["assertions"]


def test_nearest_directory_override_and_keyword_only_fixture():
    test = 'def test_value(*, api):\n    assert api.result()["value"] == 1\n'
    files = {
        "api.py": SOURCE,
        "tests/conftest.py": FIXTURE.replace("return API()", 'return "unrelated"'),
        "tests/sub/conftest.py": FIXTURE,
        "tests/sub/test_api.py": test,
    }
    assert analyze(files)["assertions"]


def test_integrated_patch_recommendation_and_cached_retrieval(tmp_path, monkeypatch):
    monkeypatch.setenv("AMU_HOME", str(tmp_path / "private"))
    files = {"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": TEST}
    for path, source in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    patch = "".join(
        difflib.unified_diff(
            SOURCE.splitlines(True),
            SOURCE.replace('"value": 1', '"value": 2').splitlines(True),
            fromfile="a/api.py",
            tofile="b/api.py",
        )
    )
    request = impact.PatchRequest(patch, base="worktree", section="recommended_checks")
    count = 0
    original = value_flow.analyze_repository

    def analyze_count(*args, **kwargs):
        nonlocal count
        count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(value_flow, "analyze_repository", analyze_count)
    packet = impact.execute_patch(str(tmp_path), request)
    rows = packet["details"]["items"]
    row = next(r for r in rows if r["test_id"] == "tests/test_api.py::test_value")
    assert row["test_source_phase"] == "before"
    assert row["verification_category"] == "related-result-assertion"
    assert row["primary_verification"] is True
    assert row["coverage_proven"] is False
    assert impact.execute_patch(str(tmp_path), request)["report_hash"] == packet["report_hash"]
    assert count == 1
    (tmp_path / "tests/conftest.py").write_text(FIXTURE.replace("return API()", 'return "unrelated"'))
    with pytest.raises(ValueError, match="stale"):
        impact.execute_patch(
            str(tmp_path), replace(request, cursor=packet["retrieval"]["recommended_checks"]["cursor"])
        )
    assert count == 2


@pytest.mark.parametrize("name,config", [
    ("pytest.ini", '[pytest]\nusefixtures = mutator\n'),
    ("setup.cfg", '[tool:pytest]\naddopts = -p mutator\n'),
    ("tox.ini", '[pytest]\nusefixtures = mutator\n'),
    ("pyproject.toml", '[tool.pytest.ini_options]\nusefixtures = ["mutator"]\n'),
])
def test_implicit_fixture_config_is_bound_and_blocks_promotion(tmp_path, monkeypatch, name, config):
    monkeypatch.setenv("AMU_HOME", str(tmp_path / "private"))
    for path, source in {"api.py": SOURCE, "tests/conftest.py": FIXTURE, "tests/test_api.py": TEST}.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    patch = "".join(difflib.unified_diff(SOURCE.splitlines(True), SOURCE.replace('"value": 1', '"value": 2').splitlines(True), fromfile="a/api.py", tofile="b/api.py"))
    request = impact.PatchRequest(patch, base="worktree", section="recommended_checks")
    old = impact.execute_patch(str(tmp_path), request)
    assert any(r["test_id"] == "tests/test_api.py::test_value" for r in old["details"]["items"])
    (tmp_path / name).write_text(config)
    new = impact.execute_patch(str(tmp_path), request)
    assert new["report_hash"] != old["report_hash"]
    assert not any(r.get("verification_category") == "related-result-assertion" for r in new["details"]["items"])


def test_fixture_runtime_limit_invalidates_report_binding(tmp_path, monkeypatch):
    from amu import pytest_fixtures, report_store
    (tmp_path / "api.py").write_text(SOURCE)
    patch = "".join(difflib.unified_diff(SOURCE.splitlines(True), SOURCE.replace('"value": 1', '"value": 2').splitlines(True), fromfile="a/api.py", tofile="b/api.py"))
    request = impact.PatchRequest(patch, base="worktree")
    old = report_store.fingerprint(tmp_path, request)
    monkeypatch.setattr(pytest_fixtures, "MAX_FIXTURE_HOPS", 1)
    assert report_store.fingerprint(tmp_path, request) != old
