from amu.ci.render import percent


def test_percent():
    assert percent(1, 4) == '25%' and percent(0, 0) == '-'
