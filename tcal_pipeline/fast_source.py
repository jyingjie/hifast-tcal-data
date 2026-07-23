#!/usr/bin/env python3
"""Read noise-diode calibration entries from the FAST website."""

from __future__ import annotations

import base64
import json
import re
import secrets
import ssl
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.request import Request, urlopen


DEFAULT_ORIGIN = "https://fast.bao.ac.cn"
DEFAULT_CATEGORY = "Noise_Diode_Calibration_Report_en"
USER_AGENT = "hifast-tcal-data-updater/1.0"


class UpstreamError(RuntimeError):
    """Raised when the FAST website cannot provide a valid source entry."""


@lru_cache(maxsize=1)
def trusted_ssl_context() -> ssl.SSLContext:
    """Use a reproducible CA bundle instead of relying on host configuration."""
    try:
        import certifi
    except ImportError as exc:
        raise UpstreamError(
            "certifi is required; install dependencies from requirements.txt"
        ) from exc
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except OSError as exc:
        raise UpstreamError("Could not load the trusted CA certificate bundle") from exc


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


@dataclass(frozen=True)
class ArticleSource:
    article_id: str
    title: str
    created_at: str
    updated_at: str
    calibration_date: str | None
    report_month: str | None
    date_source: str | None
    report_url: str
    high_url: str
    low_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _add_cache_buster(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_t", str(int(time.time() * 1000))))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _read_response(request: Request, timeout: float, retries: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(
                request,
                timeout=timeout,
                context=trusted_ssl_context(),
            ) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise UpstreamError(f"FAST website request failed: {last_error}") from last_error


class FastWebsiteClient:
    """Small client for the API used by the FAST website frontend."""

    def __init__(
        self,
        origin: str = DEFAULT_ORIGIN,
        *,
        timeout: float = 30,
        retries: int = 3,
    ) -> None:
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session_id = str(int(time.time() * 1000))
        self._public_key: Any = None

    def _headers(self) -> dict[str, str]:
        return {
            "Fastwww-Locale": "en-US",
            "Encrypt-Session": self.session_id,
            "User-Agent": USER_AGENT,
        }

    def _request_json(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        request = Request(_add_cache_buster(url), headers=request_headers, method="GET")
        raw = _read_response(request, self.timeout, self.retries)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"FAST website returned invalid JSON for {url}") from exc

    def _load_public_key(self) -> None:
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_der_public_key,
            )
        except ImportError as exc:
            raise UpstreamError(
                "cryptography is required; install dependencies from requirements.txt"
            ) from exc

        payload = self._request_json(
            f"{self.origin}/api/encryptCommon/v1/getKey"
        )
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise UpstreamError(f"FAST website key request failed: {payload!r}")
        encoded = payload.get("data")
        if not isinstance(encoded, str):
            raise UpstreamError("FAST website key response does not contain a public key")
        try:
            der = base64.b64decode("".join(encoded.split()), validate=True)
            self._public_key = load_der_public_key(der)
        except (ValueError, TypeError) as exc:
            raise UpstreamError("FAST website returned an invalid public key") from exc

    def _encrypted_get(self, path: str, *, allow_key_retry: bool = True) -> Any:
        try:
            from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives.padding import PKCS7
        except ImportError as exc:
            raise UpstreamError(
                "cryptography is required; install dependencies from requirements.txt"
            ) from exc

        if self._public_key is None:
            self._load_public_key()

        aes_key = secrets.token_hex(16).encode("ascii")
        try:
            encrypted_key = self._public_key.encrypt(
                aes_key,
                asym_padding.PKCS1v15(),
            )
        except (TypeError, ValueError) as exc:
            raise UpstreamError("Could not encrypt the FAST website session key") from exc

        payload = self._request_json(
            urljoin(f"{self.origin}/", path.lstrip("/")),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Encrypt-Key": base64.b64encode(encrypted_key).decode("ascii"),
            },
        )

        if isinstance(payload, dict):
            if payload.get("code") == 888 and allow_key_retry:
                self._public_key = None
                self._load_public_key()
                return self._encrypted_get(path, allow_key_retry=False)
            raise UpstreamError(f"FAST website API returned an error: {payload!r}")
        if not isinstance(payload, str):
            raise UpstreamError("FAST website returned an unknown encrypted response")

        try:
            encrypted_data = base64.b64decode(payload, validate=True)
            decryptor = Cipher(
                algorithms.AES(aes_key),
                modes.CBC(aes_key[:16]),
            ).decryptor()
            padded = decryptor.update(encrypted_data) + decryptor.finalize()
            unpadder = PKCS7(128).unpadder()
            plain = unpadder.update(padded) + unpadder.finalize()
            return json.loads(plain.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpstreamError("Could not decrypt the FAST website response") from exc

    def list_articles(self, category: str = DEFAULT_CATEGORY) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            payload = self._encrypted_get(
                f"/api/cms/category/{category}"
                f"?pageNum={page}&pageSize={page_size}"
            )
            if not isinstance(payload, dict) or payload.get("code") != 200:
                raise UpstreamError(f"Invalid FAST article list response: {payload!r}")
            page_rows = payload.get("rows")
            if not isinstance(page_rows, list):
                raise UpstreamError("FAST article list does not contain rows")
            rows.extend(row for row in page_rows if isinstance(row, dict))
            total = payload.get("total")
            if not isinstance(total, int) or len(rows) >= total or not page_rows:
                return rows
            page += 1

    def get_article(self, article_id: str) -> dict[str, Any]:
        payload = self._encrypted_get(f"/api/cms/article/{article_id}")
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise UpstreamError(f"Invalid FAST article response: {payload!r}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise UpstreamError(f"FAST article {article_id} does not contain data")
        return data


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _normalize_url(origin: str, link: str) -> str:
    absolute = urljoin(f"{origin}/", link)
    parts = urlsplit(absolute)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%:@"),
            parts.query,
            parts.fragment,
        )
    )


def _full_date(text: str) -> str | None:
    for value in re.findall(r"(?<!\d)(20\d{6})(?!\d)", unquote(text)):
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError:
            continue
        return value
    return None


def _report_month(text: str) -> str | None:
    match = re.search(r"(?<!\d)(20\d{4})(?!\d)", unquote(text))
    return match.group(1) if match else None


def article_to_source(
    article: dict[str, Any],
    *,
    origin: str = DEFAULT_ORIGIN,
) -> ArticleSource:
    html = article.get("bodyHtml") or ""
    if not isinstance(html, str):
        raise UpstreamError(f"Article {article.get('id')} has invalid HTML")
    parser = _LinkParser()
    parser.feed(html)
    links = _unique(_normalize_url(origin, link) for link in parser.links)

    report_urls = [url for url in links if urlsplit(url).path.lower().endswith(".pdf")]
    archive_urls = [
        url for url in links if urlsplit(url).path.lower().endswith(".tar.gz")
    ]
    high_urls = [
        url
        for url in archive_urls
        if "high" in unquote(urlsplit(url).path.rsplit("/", 1)[-1]).lower()
    ]
    low_urls = [
        url
        for url in archive_urls
        if "low" in unquote(urlsplit(url).path.rsplit("/", 1)[-1]).lower()
    ]
    if not report_urls or not high_urls or not low_urls:
        raise UpstreamError(
            f"Article {article.get('id')} does not contain PDF, high and low links"
        )

    report_url = report_urls[0]
    title = str(article.get("title") or "")
    calibration_date = _full_date(report_url)
    date_source = "report_url" if calibration_date else None
    month = _report_month(report_url) or _report_month(title)
    if calibration_date and month and not calibration_date.startswith(month):
        raise UpstreamError(
            f"Article {article.get('id')} month {month} conflicts with "
            f"PDF filename date {calibration_date}"
        )

    return ArticleSource(
        article_id=str(article.get("id") or ""),
        title=title,
        created_at=str(article.get("createTime") or ""),
        updated_at=str(article.get("updateTime") or ""),
        calibration_date=calibration_date,
        report_month=month,
        date_source=date_source,
        report_url=report_url,
        high_url=high_urls[0],
        low_url=low_urls[0],
    )


def resolve_source_pdf_date(
    source: ArticleSource,
    *,
    timeout: float = 60,
    retries: int = 3,
    max_pdf_bytes: int = 50 * 1024 * 1024,
) -> ArticleSource:
    if source.calibration_date is not None:
        return source
    request = Request(
        source.report_url,
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    raw = _read_response(request, timeout, retries)
    if not raw.startswith(b"%PDF-"):
        raise UpstreamError(f"{source.report_url} did not return a PDF")
    if len(raw) > max_pdf_bytes:
        raise UpstreamError(
            f"{source.report_url} exceeds the {max_pdf_bytes}-byte PDF limit"
        )
    try:
        from .pdf_date import PdfDateError, extract_pdf_test_date

        with tempfile.NamedTemporaryFile(suffix=".pdf") as temporary:
            temporary.write(raw)
            temporary.flush()
            date = extract_pdf_test_date(Path(temporary.name))
        if source.report_month and not date.startswith(source.report_month):
            raise PdfDateError(
                f"PDF test date {date} conflicts with article month "
                f"{source.report_month}"
            )
    except (OSError, PdfDateError) as exc:
        raise UpstreamError(
            f"Could not determine the test date from {source.report_url}: {exc}"
        ) from exc
    return replace(
        source,
        calibration_date=date,
        date_source="pdf_text",
    )


def discover_sources(
    *,
    client: FastWebsiteClient | None = None,
    resolve_pdf_dates: bool = True,
) -> list[ArticleSource]:
    website = client or FastWebsiteClient()
    sources: list[ArticleSource] = []
    for summary in website.list_articles():
        article_id = str(summary.get("id") or "")
        if not article_id:
            raise UpstreamError("FAST article list contains an entry without an ID")
        article = website.get_article(article_id)
        source = article_to_source(article, origin=website.origin)
        if resolve_pdf_dates and source.calibration_date is None:
            source = resolve_source_pdf_date(source)
        sources.append(source)
    return sources
