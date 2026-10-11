import pytest
from threshold import admits


def test_target_import_path():
    assert callable(admits)
