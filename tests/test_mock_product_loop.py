from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.mock_product_loop import run_mock


class MockProductLoopTests(unittest.TestCase):
    def test_two_users_fifteen_tickets_complete_the_product_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_mock(Path(temporary) / "exercise")

        self.assertEqual(result["tickets"], 15)
        self.assertTrue(result["sync"]["repeat"]["revision_unchanged"])
        self.assertEqual(result["sync"]["changed"]["updated"], 3)
        self.assertEqual(result["sparse"]["before_open"], [])
        self.assertEqual(result["sparse"]["after_open"], ["DEMO-7"])
        self.assertTrue(result["doctor"]["ada_ready"])
        self.assertTrue(result["doctor"]["lin_ready"])
        self.assertTrue(all(result["assertions"].values()))


if __name__ == "__main__":
    unittest.main()
