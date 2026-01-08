from config import settings


def _book_weight(label: str) -> float:
    if not label:
        return settings.BOOK_WEIGHT_DEFAULT
    key = label.replace(" ", "").upper()
    return settings.BOOK_WEIGHT_OVERRIDES.get(key, settings.BOOK_WEIGHT_DEFAULT)
