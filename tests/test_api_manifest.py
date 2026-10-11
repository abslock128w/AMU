import subprocess

import pytest

from amu import api_manifest


def test_config_defaults_and_response_shapes_are_static_review_signals():
    result = api_manifest.compare_sources('{"timeout": 1, "response": {"value": 0}}',
                                          '{"timeout": 2, "response": {"value": "text"}}', "settings.json")
    assert result["complete"] is True
    assert {item["id"] for item in result["changes"]} == {"settings.json#timeout", "settings.json#response.value"}
    assert all(item["change"] == "surface" and item["confidence"] == "requires_verification" for item in result["changes"])


def test_secret_defaults_remain_comparable_without_literal_disclosure():
    result = api_manifest.compare_sources('{"password": "FIRST_PRIVATE_VALUE"}',
                                          '{"password": "SECOND_PRIVATE_VALUE"}', "settings.json")
    from amu.harness.context import encode
    assert "PRIVATE_VALUE" not in encode(result)
    assert result["changes"]


def test_secret_defaults_inside_configuration_arrays_are_redacted():
    result = api_manifest.analyze('{"accounts":[{"password":"NESTED_PRIVATE_VALUE"}]}', "settings.json")
    from amu.harness.context import encode
    assert "NESTED_PRIVATE_VALUE" not in encode(result)


@pytest.mark.parametrize("source", [
    '{"accounts":[{"credential":"FAKE_PRIVATE_VALUE"}]}',
    '{"accounts":[{"secret":{"public_label":"FAKE_PRIVATE_VALUE"}}]}',
    '{"passwd":"FAKE_PRIVATE_VALUE","api-key":"FAKE_PRIVATE_VALUE"}',
    '{"accounts":[{"apiKey":{"public_label":"FAKE_PRIVATE_VALUE"}}]}',
    '{"accounts":[{"private_key":{"public_label":"FAKE_PRIVATE_VALUE"}}]}',
    '{"accounts":[{"accessKey":{"public_label":"FAKE_PRIVATE_VALUE"}}]}',
])
def test_nested_secret_named_bindings_are_redacted_structurally(source):
    from amu.harness.context import encode
    assert "FAKE_PRIVATE_VALUE" not in encode(api_manifest.analyze(source, "settings.json"))


@pytest.mark.parametrize("path,before,after", [
    ("settings.json", '{"":1}', '{"":2}'),
    ("settings.toml", '"" = 1', '"" = 2'),
])
def test_empty_configuration_key_changes_are_not_silently_dropped(path, before, after):
    result = api_manifest.compare_sources(before, after, path)
    assert result["complete"] and len(result["changes"]) == 1
    assert result["changes"][0]["id"] == f"{path}#\\e"


def test_oversized_configuration_key_has_explicit_incomplete_coverage():
    import json
    source = json.dumps({"a" * 10000: {str(i): i for i in range(1000)}})
    result = api_manifest.analyze(source, "settings.json")
    assert result["status"] == "partial" and result["unknowns"]
    assert len(json.dumps(result).encode()) < api_manifest.MAX_MANIFEST_BYTES


def test_configuration_manifest_output_cannot_amplify_past_byte_budget():
    import json
    source = json.dumps({"a" * 1500: {str(i): i for i in range(4000)}})
    result = api_manifest.analyze(source, "settings.json")
    assert result["status"] == "partial" and result["unknowns"]
    assert len(json.dumps(result).encode()) <= api_manifest.MAX_MANIFEST_BYTES


def test_empty_key_escape_does_not_collide_with_literal_escape_sequence():
    manifest = api_manifest.analyze('{"":1,"\\\\e":2}', "settings.json")
    assert {row["id"] for row in manifest["entries"]} == {"settings.json#\\e", "settings.json#\\\\e"}


@pytest.mark.parametrize("source,path", [("export function f() {}", "api.ts"), ("{bad", "settings.json")])
def test_unsupported_or_malformed_input_is_an_abstention(source, path):
    result = api_manifest.analyze(source, path)
    assert result["unknowns"] and result["status"] != "complete"


def test_duplicate_configuration_keys_do_not_become_false_no_change():
    assert api_manifest.analyze('{"timeout":1,"timeout":2}', "settings.json")["status"] == "partial"


@pytest.mark.parametrize("source,path", [('{"value":1e999}', "settings.json"),
                                         ('value = inf', "settings.toml"),
                                         ('value = [nan]', "settings.toml")])
def test_nonfinite_configuration_values_remain_unknown(source, path):
    assert api_manifest.analyze(source, path)["status"] == "partial"


def test_literal_dotted_keys_do_not_collide_with_nested_keys():
    manifest = api_manifest.analyze('{"a.b":1,"a":{"b":2}}', "settings.json")
    assert {row["id"] for row in manifest["entries"]} == {"settings.json#a\\.b", "settings.json#a.b"}
    result = api_manifest.compare_sources('{"a.b":1,"a":{"b":2}}',
                                          '{"a.b":3,"a":{"b":2}}', "settings.json")
    assert [row["id"] for row in result["changes"]] == ["settings.json#a\\.b"]


def test_report_reader_is_bounded_and_does_not_follow_source_symlinks(tmp_path):
    source = tmp_path / "settings.json"
    source.write_text('{"value":1}')
    (tmp_path / "alias.json").symlink_to(source)
    report = api_manifest.report(str(tmp_path), "alias.json")
    assert report["after"]["status"] == "partial"
    assert report["unknowns"]


def _git(tmp_path, *args):
    return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()


def _repository(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "--allow-empty", "-qm", "baseline")


def test_report_detects_new_and_deleted_files_from_verified_absence(tmp_path):
    _repository(tmp_path)
    source = tmp_path / "api.py"
    source.write_text("def public():\n    return 1\n")
    result = api_manifest.report(str(tmp_path), "api.py")
    assert result["complete"] and [row["change"] for row in result["changes"]] == ["added"]
    _git(tmp_path, "add", "api.py")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "api")
    source.unlink()
    result = api_manifest.report(str(tmp_path), "api.py")
    assert result["complete"] and [row["change"] for row in result["changes"]] == ["removed"]
    assert result["source_capture"]["baseline"]["revision"] == _git(tmp_path, "rev-parse", "HEAD")
    assert result["source_capture"]["current"]["status"] == "absent"


def test_report_does_not_parse_committed_symlink_as_source(tmp_path):
    _repository(tmp_path)
    source = tmp_path / "alias.py"
    source.symlink_to("missing_target")
    _git(tmp_path, "add", "alias.py")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "alias")
    source.unlink()
    source.write_text("def public():\n    return 1\n")
    result = api_manifest.report(str(tmp_path), "alias.py")
    assert not result["complete"] and result["before"]["unknowns"]


def test_missing_file_under_dangling_parent_alias_is_not_verified_absence(tmp_path):
    _repository(tmp_path)
    (tmp_path / "linked").symlink_to(tmp_path / "missing", target_is_directory=True)
    result = api_manifest.report(str(tmp_path), "linked/api.py")
    assert not result["complete"] and result["after"]["unknowns"]
