#!/usr/bin/env python3
"""Extract the calibration test date from a FAST PDF report."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


class PdfDateError(RuntimeError):
    """Raised when a PDF does not provide one unambiguous test date."""


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_PATTERN = "|".join(MONTHS)
TEXT_DATE_PATTERN = re.compile(
    rf"\b(?P<month>{MONTH_PATTERN})\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s*,?\s+"
    r"(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
NUMERIC_DATE_PATTERN = re.compile(
    r"\b(?P<year>20\d{2})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})\b"
)


def _date_value(year: int, month: int, day: int) -> str | None:
    try:
        return datetime(year, month, day).strftime("%Y%m%d")
    except ValueError:
        return None


def document_dates(text: str) -> list[tuple[str, int, int]]:
    dates: list[tuple[str, int, int]] = []
    for match in TEXT_DATE_PATTERN.finditer(text):
        value = _date_value(
            int(match.group("year")),
            MONTHS[match.group("month").lower()],
            int(match.group("day")),
        )
        if value:
            dates.append((value, match.start(), match.end()))
    for match in NUMERIC_DATE_PATTERN.finditer(text):
        value = _date_value(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        if value:
            dates.append((value, match.start(), match.end()))
    return sorted(dates, key=lambda item: item[1])


def extract_test_date_from_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[tuple[int, str]] = []
    for value, start, end in document_dates(normalized):
        before = normalized[max(0, start - 700) : start].lower()
        after = normalized[end : min(len(normalized), end + 160)].lower()
        paragraph_start = max(before.rfind("\n\n"), before.rfind("."))
        local_before = before[paragraph_start + 1 :]

        has_test_word = bool(
            re.search(
                r"\b(test|measurement|testing|calibration|observation)\b",
                local_before,
            )
        )
        has_action = bool(
            re.search(
                r"\b(performed|conducted|carried\s+out|undertook|measured)\b",
                local_before,
            )
        )
        has_time_word = bool(
            re.search(r"\b(between|during|on|from|at)\b", local_before)
        )
        date_followed_by_test = bool(
            re.search(r"^\s*(?:\([^)]*\)\s*)?.{0,60}\btest\b", after)
        )

        score = 0
        if has_test_word and has_action:
            score += 4
        if has_test_word and has_time_word:
            score += 2
        if date_followed_by_test:
            score += 2
        if score:
            candidates.append((score, value))

    if not candidates:
        raise PdfDateError("PDF text does not contain a date linked to a test")
    highest_score = max(score for score, _ in candidates)
    best_dates = sorted(
        {value for score, value in candidates if score == highest_score}
    )
    if len(best_dates) != 1:
        raise PdfDateError(
            "PDF text contains multiple equally likely test dates: "
            + ", ".join(best_dates)
        )
    return best_dates[0]


def extract_pdf_test_date(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfDateError(
            "pypdf is required; install dependencies from requirements.txt"
        ) from exc

    try:
        reader = PdfReader(path)
        text = "\n\n".join(
            page.extract_text() or "" for page in reader.pages[:2]
        )
    except Exception as exc:
        raise PdfDateError(f"Could not extract text from PDF {path}") from exc
    if not text.strip():
        raise PdfDateError(f"PDF {path} does not contain extractable text")
    return extract_test_date_from_text(text)


def verify_pdf_test_date(path: Path, expected_date: str) -> str:
    actual_date = extract_pdf_test_date(path)
    if actual_date != expected_date:
        raise PdfDateError(
            f"PDF test date is {actual_date}, but the expected date is {expected_date}"
        )
    return actual_date
