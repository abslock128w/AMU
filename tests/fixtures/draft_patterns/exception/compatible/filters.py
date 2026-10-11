def parse_window(value):
    if value >= 0:
        return value + 1
    raise ValueError("window must be nonnegative")


def unrelated():
    return "stable"
