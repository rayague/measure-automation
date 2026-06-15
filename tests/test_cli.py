from __future__ import annotations

import argparse
import os
import unittest
from unittest.mock import patch

from boundary_analyzer.cli import _validate_duration, _validate_port, _validate_positive_int, _validate_threshold, main


class CliValidatorsTest(unittest.TestCase):
    def test_validate_port_ok(self):
        self.assertEqual(_validate_port("80"), 80)

    def test_validate_port_too_low(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_port("0")

    def test_validate_port_too_high(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_port("65536")

    def test_validate_threshold_ok(self):
        self.assertEqual(_validate_threshold("0.5"), 0.5)

    def test_validate_threshold_too_low(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_threshold("-0.1")

    def test_validate_threshold_too_high(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_threshold("1.5")

    def test_validate_duration_ok(self):
        self.assertEqual(_validate_duration("10"), 10)

    def test_validate_duration_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_duration("0")

    def test_validate_duration_negative(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_duration("-5")

    def test_validate_positive_int_ok(self):
        self.assertEqual(_validate_positive_int("3"), 3)

    def test_validate_positive_int_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _validate_positive_int("0")


class CliVersionTest(unittest.TestCase):
    def test_version_flag(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_command_shows_help(self):
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertNotEqual(ctx.exception.code, 0)


class CliExceptionHandlerTest(unittest.TestCase):
    @patch("boundary_analyzer.cli._main", side_effect=ValueError("oops"))
    def test_unexpected_exception_returns_1(self, mock_main):
        rc = main([])
        self.assertEqual(rc, 1)

    @patch("boundary_analyzer.cli._main", side_effect=ValueError("oops"))
    def test_unexpected_exception_debug(self, mock_main):
        with patch.dict(os.environ, {"MBA_DEBUG": "1"}):
            rc = main([])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
