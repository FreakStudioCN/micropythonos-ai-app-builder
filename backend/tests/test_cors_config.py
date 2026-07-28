import os
import unittest
from unittest import mock

from app.cors import compute_frontend_origins as _frontend_origins


class FrontendOriginsTest(unittest.TestCase):
    def test_dev_origins_present_by_default(self) -> None:
        with mock.patch.dict(os.environ):
            for name in ("MPOS_ALLOW_DEV_ORIGINS", "FRONTEND_ORIGINS", "FRONTEND_ORIGIN"):
                os.environ.pop(name, None)
            origins = _frontend_origins()
        self.assertIn("http://localhost:5173", origins)
        self.assertIn("http://127.0.0.1:5174", origins)

    def test_dev_origins_removed_when_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"MPOS_ALLOW_DEV_ORIGINS": "false"}):
            for name in ("FRONTEND_ORIGINS", "FRONTEND_ORIGIN"):
                os.environ.pop(name, None)
            origins = _frontend_origins()
        self.assertEqual(origins, [])

    def test_configured_origins_survive_disabled_dev(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "MPOS_ALLOW_DEV_ORIGINS": "no",
                "FRONTEND_ORIGINS": "https://mpos.upypi.net",
            },
        ):
            origins = _frontend_origins()
        self.assertEqual(origins, ["https://mpos.upypi.net"])


if __name__ == "__main__":
    unittest.main()
