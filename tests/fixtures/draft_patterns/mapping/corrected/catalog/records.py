def lookup_record(label):
    if label in {"sample", "none-status", "missing-status"}:
        if label == "sample":
            return {"status": "active", "note": None}
        if label == "none-status":
            return {"status": None, "note": None}
        return {"note": None}
    return {"status": "unknown", "note": None}


def unrelated():
    return "updated independently"
