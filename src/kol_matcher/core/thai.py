import re

THAI_CHAR = re.compile(r"[\u0E00-\u0E7F]")


def has_thai(text: str | None) -> bool:
    return bool(text) and THAI_CHAR.search(text) is not None
