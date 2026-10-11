import pytest
from selection import selected


def test_target_import_path():
    assert callable(selected)
