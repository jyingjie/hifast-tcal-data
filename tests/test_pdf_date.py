import unittest

from tcal_pipeline.pdf_date import PdfDateError, extract_test_date_from_text


class PdfDateTests(unittest.TestCase):
    def test_selects_test_date_instead_of_report_date(self):
        text = """
        Test Report of the Noise Diode on the 19-Beam Receiver
        July 13, 2026

        We performed a noise temperature test on the noise diode of the
        FAST 19-beam receiver between 09:50-14:55, July 08, 2026 (BJT),
        in order to provide a reference for calibrating observed data.
        """

        self.assertEqual(extract_test_date_from_text(text), "20260708")

    def test_rejects_date_without_test_context(self):
        with self.assertRaises(PdfDateError):
            extract_test_date_from_text("Report published on July 13, 2026.")


if __name__ == "__main__":
    unittest.main()
