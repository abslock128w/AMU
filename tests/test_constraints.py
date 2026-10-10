from pathlib import Path

import pytest

from scripts.check_constraints import check, read_pins

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_constraints_are_exact():
    pins = read_pins(ROOT / 'constraints.txt')
    assert {'anthropic', 'mcp', 'pytest', 'build', 'setuptools', 'watchfiles'} <= pins.keys()


def test_detects_drift_and_unpinned_transitive_dependency():
    assert check({'rich': '15.0.0'}, [('rich', '15.0.0'), ('pip', '26.1')]) == []
    assert len(check({'rich': '15.0.0'}, [('rich', '14.0.0'), ('new-dependency', '1')])) == 2
    assert check({'prompt-toolkit': '3.0.53'}, [('prompt_toolkit', '3.0.53')]) == []


@pytest.mark.parametrize('text', ['', 'rich>=15', 'rich==15\nrich==16'])
def test_invalid_constraints_rejected(tmp_path, text):
    path = tmp_path / 'constraints.txt'
    path.write_text(text)
    with pytest.raises(ValueError):
        read_pins(path)
