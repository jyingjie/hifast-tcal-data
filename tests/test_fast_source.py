import unittest
from unittest.mock import patch

from tcal_pipeline.fast_source import (
    ArticleSource,
    USER_AGENT,
    UpstreamError,
    article_to_source,
    resolve_source_pdf_date,
)


class ArticleSourceTests(unittest.TestCase):
    def test_uses_linux_chrome_user_agent(self):
        self.assertIn("Mozilla/5.0 (X11; Linux x86_64)", USER_AGENT)
        self.assertIn("Chrome/150.0.0.0", USER_AGENT)
        self.assertTrue(USER_AGENT.endswith("Safari/537.36"))

    def test_extracts_latest_article_links_and_date(self):
        article = {
            "id": "2076595297868402689",
            "title": "Noise Diode Calibration Report-202607",
            "createTime": "2026-07-13 17:11:15",
            "updateTime": "2026-07-13 17:11:23",
            "bodyHtml": """
                <a href="/files/noise_test_20260708_en.pdf">report</a>
                <a href="/files/high_202607.tar.gz">high</a>
                <a href="/files/high_202607.tar.gz">high duplicate</a>
                <a href="/files/low_202607.tar.gz">low</a>
            """,
        }

        source = article_to_source(article, origin="https://example.test")

        self.assertEqual(source.calibration_date, "20260708")
        self.assertEqual(source.date_source, "report_url")
        self.assertEqual(
            source.high_url,
            "https://example.test/files/high_202607.tar.gz",
        )

    def test_does_not_infer_day_from_existing_manifest(self):
        article = {
            "id": "1",
            "title": "Noise Diode Calibration Report-202503",
            "bodyHtml": """
                <a href="/files/report-202503.pdf">report</a>
                <a href="/files/high_202503.tar.gz">high</a>
                <a href="/files/low_202503.tar.gz">low</a>
            """,
        }

        source = article_to_source(article, origin="https://example.test")

        self.assertIsNone(source.calibration_date)
        self.assertIsNone(source.date_source)

    def test_resolves_month_only_report_with_package_pdf_parser(self):
        source = ArticleSource(
            article_id="1",
            title="Noise Diode Calibration Report-202503",
            created_at="",
            updated_at="",
            calibration_date=None,
            report_month="202503",
            date_source=None,
            report_url="https://example.test/report-202503.pdf",
            high_url="https://example.test/high.tar.gz",
            low_url="https://example.test/low.tar.gz",
        )
        with (
            patch(
                "tcal_pipeline.fast_source._read_response",
                return_value=b"%PDF-1.7 test",
            ),
            patch(
                "tcal_pipeline.pdf_date.extract_pdf_test_date",
                return_value="20250329",
            ),
        ):
            resolved = resolve_source_pdf_date(source)

        self.assertEqual(resolved.calibration_date, "20250329")
        self.assertEqual(resolved.date_source, "pdf_text")

    def test_rejects_article_without_both_archives(self):
        article = {
            "id": "1",
            "title": "Noise Diode Calibration Report-202607",
            "bodyHtml": """
                <a href="/files/noise_test_20260708_en.pdf">report</a>
                <a href="/files/high_202607.tar.gz">high</a>
            """,
        }

        with self.assertRaises(UpstreamError):
            article_to_source(article, origin="https://example.test")

    def test_rejects_filename_date_that_conflicts_with_article_month(self):
        article = {
            "id": "1",
            "title": "Noise Diode Calibration Report-202607",
            "bodyHtml": """
                <a href="/files/noise_test_20260801_en.pdf">report</a>
                <a href="/files/high_202607.tar.gz">high</a>
                <a href="/files/low_202607.tar.gz">low</a>
            """,
        }

        with self.assertRaises(UpstreamError):
            article_to_source(article, origin="https://example.test")


if __name__ == "__main__":
    unittest.main()
