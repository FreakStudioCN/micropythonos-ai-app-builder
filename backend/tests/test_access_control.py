import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import AuthService, COOKIE_NAME, login_sessions, users
from app.billing import BillingService
from app.session_service import SessionService
import app.main as main_module
import app.session_service as session_module


class AccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_root = session_module.SESSION_ROOT
        self.original_session_service = main_module.session_service
        self.original_billing_service = main_module.billing_service
        self.original_auth_service = main_module.auth_service

        session_module.SESSION_ROOT = Path(self.temp.name, "sessions").resolve()
        auth = AuthService(f"sqlite:///{Path(self.temp.name, 'auth.db')}")
        main_module.auth_service = auth
        main_module.billing_service = BillingService(engine=auth.engine)
        main_module.session_service = SessionService()
        self.client_a = TestClient(main_module.app)
        self.client_b = TestClient(main_module.app)

    def tearDown(self) -> None:
        self.client_a.close()
        self.client_b.close()
        main_module.session_service = self.original_session_service
        main_module.billing_service = self.original_billing_service
        main_module.auth_service = self.original_auth_service
        session_module.SESSION_ROOT = self.original_root
        self.temp.cleanup()

    def _register(
        self,
        client: TestClient,
        username: str,
        password: str = "correct-horse-123",
    ) -> dict:
        response = client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        return response.json()

    @staticmethod
    def _session_payload() -> dict:
        return {
            "idempotency_key": "access-create-0001",
            "prompt": "做一个隔离测试应用",
            "package_name": "com.example.access_test",
            "targets": ["package-only"],
        }

    def test_registration_hashes_password_and_restores_login(self) -> None:
        account = self._register(self.client_a, "maker_a")
        self.assertEqual(account["credits"], 50)
        self.assertEqual(account["username"], "maker_a")
        self.assertIn(COOKIE_NAME, self.client_a.cookies)

        with main_module.auth_service.engine.connect() as connection:
            user = connection.execute(select(users)).mappings().one()
            login_session = connection.execute(select(login_sessions)).mappings().one()
        self.assertNotEqual(user["password_hash"], "correct-horse-123")
        self.assertTrue(user["password_hash"].startswith("scrypt$"))
        self.assertNotEqual(
            login_session["token_hash"],
            self.client_a.cookies.get(COOKIE_NAME),
        )
        self.assertEqual(len(login_session["token_hash"]), 64)

        duplicate = self.client_b.post(
            "/api/auth/register",
            json={"username": "MAKER_A", "password": "another-password-123"},
        )
        self.assertEqual(duplicate.status_code, 409)

        wrong = self.client_b.post(
            "/api/auth/login",
            json={"username": "maker_a", "password": "wrong-password"},
        )
        self.assertEqual(wrong.status_code, 401)
        login = self.client_b.post(
            "/api/auth/login",
            json={"username": "maker_a", "password": "correct-horse-123"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["user_id"], account["user_id"])

        logout = self.client_b.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client_b.get("/api/user").status_code, 401)

    def test_sessions_artifacts_and_permissions_are_isolated(self) -> None:
        self._register(self.client_a, "maker_a")
        self._register(self.client_b, "maker_b")
        created_response = self.client_a.post(
            "/api/sessions",
            json=self._session_payload(),
        )
        self.assertEqual(created_response.status_code, 201)
        created = created_response.json()
        session_id = created["session_id"]
        artifact_id = created["artifacts"][0]["id"]
        permission_id = created["permissions"][0]["permission_id"]

        self.assertEqual(self.client_a.get(f"/api/sessions/{session_id}").status_code, 200)
        self.assertEqual(len(self.client_a.get("/api/sessions").json()), 1)
        self.assertEqual(self.client_b.get("/api/sessions").json(), [])
        self.assertEqual(self.client_b.get(f"/api/sessions/{session_id}").status_code, 404)
        self.assertEqual(self.client_b.get(f"/api/artifacts/{artifact_id}").status_code, 404)
        self.assertEqual(
            self.client_b.post(
                f"/api/permissions/{permission_id}/decision",
                json={
                    "idempotency_key": "access-permission-0001",
                    "decision": "allow_once",
                },
            ).status_code,
            404,
        )

    def test_billing_identity_cannot_be_selected_by_query_or_header(self) -> None:
        account_a = self._register(self.client_a, "maker_a")
        account_b = self._register(self.client_b, "maker_b")

        selected = self.client_a.get(
            "/api/billing/account?user_id=attacker-selected-user",
            headers={"X-MPOS-User-ID": account_b["user_id"]},
        ).json()
        self.assertEqual(selected["user_id"], account_a["user_id"])
        self.assertNotEqual(selected["user_id"], account_b["user_id"])
        self.assertEqual(selected["credits"], 50)
        self.assertEqual(selected["generation_cost"], 10)
        self.assertEqual(selected["generations_remaining"], 5)

    def test_unauthenticated_response_keeps_local_cors_headers(self) -> None:
        response = self.client_a.get(
            "/api/user",
            headers={"Origin": "http://localhost:5174"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:5174",
        )
        self.assertEqual(
            response.headers.get("access-control-allow-credentials"),
            "true",
        )

    def test_secure_cookie_can_be_forced_for_https_deployment(self) -> None:
        with patch.dict("os.environ", {"MPOS_COOKIE_SECURE": "true"}):
            response = self.client_a.post(
                "/api/auth/register",
                json={
                    "username": "secure_maker",
                    "password": "correct-horse-123",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()
