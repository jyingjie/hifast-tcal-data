import unittest

from tcal_pipeline.convert_tcal import (
    ConversionError,
    expected_archive_names,
    validate_date,
)


class ConvertTcalTests(unittest.TestCase):
    def test_expected_archive_contains_frequency_and_all_beams(self):
        names = expected_archive_names("high")

        self.assertEqual(len(names), 39)
        self.assertIn("freq.dat", names)
        self.assertIn("T_noise_W_high_01a.dat", names)
        self.assertIn("T_noise_W_high_19b.dat", names)

    def test_date_validation(self):
        self.assertEqual(validate_date("20260708"), "20260708")
        with self.assertRaises(ConversionError):
            validate_date("20260230")
        with self.assertRaises(ConversionError):
            validate_date("202607")


if __name__ == "__main__":
    unittest.main()
