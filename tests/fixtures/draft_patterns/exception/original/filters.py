def parse_window(value):
    if value < 0:
        raise ValueError("window must be nonnegative")
    return value + 1


def unrelated():
    return "stable"
