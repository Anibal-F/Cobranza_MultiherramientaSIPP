import re
from datetime import date, datetime
from typing import Optional

_MONEY_RE = re.compile(r"[^0-9.\-]")


def clean_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return value.strip().strip("'").strip()


def parse_money(value: Optional[str]) -> float:
    text = clean_text(value)
    if not text:
        return 0.0
    text = _MONEY_RE.sub("", text)
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_date_ddmmyyyy(value: Optional[str]) -> Optional[date]:
    text = clean_text(value)
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%d%m%Y").date()
    except ValueError:
        return None


def parse_date_dmy_slash(value: Optional[str]) -> Optional[date]:
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None
