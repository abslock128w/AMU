def parse_window(value):
    if value >= 0:
        return value + 1
    raise ValueError("negative windows are invalid")


def unrelated():
    return "updated independently"
