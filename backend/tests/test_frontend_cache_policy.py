import unittest

from app.main import _frontend_cache_headers


class FrontendCachePolicyTests(unittest.TestCase):
    def test_html_shell_is_never_reused_after_deployment(self) -> None:
        self.assertEqual(
            _frontend_cache_headers("/", "text/html; charset=utf-8"),
            {
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    def test_vite_hashed_assets_are_immutable(self) -> None:
        self.assertEqual(
            _frontend_cache_headers(
                "/assets/index-AbCd1234.js", "text/javascript; charset=utf-8"
            ),
            {"Cache-Control": "public, max-age=31536000, immutable"},
        )

    def test_api_responses_keep_their_existing_cache_policy(self) -> None:
        self.assertEqual(
            _frontend_cache_headers("/api/health", "application/json"), {}
        )


if __name__ == "__main__":
    unittest.main()
