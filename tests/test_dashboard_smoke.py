import unittest
from pathlib import Path

from boundary_analyzer.dashboard.app import create_app


class DashboardSmokeTest(unittest.TestCase):
    def test_create_app_no_data_dir(self):
        """Creating the Dash app with no data directory should not raise."""
        app = create_app(data_dir=None)
        self.assertIsNotNone(app)

    def test_create_app_with_sample_dir(self):
        """Creating the Dash app with the sample audit output should not raise."""
        sample = Path("data/_audit_out3")
        app = create_app(data_dir=sample)
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
