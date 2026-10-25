
import os
import unittest
from unittest.mock import patch

# Set isolated test configuration BEFORE importing main.py.
# Never use production credentials or database connections here.
os.environ["ENVIRONMENT"] = "testing"
os.environ["DB_URL"] = (
    "postgresql://test:test@127.0.0.1:1/dhansutra_csrf_test"
)
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["SECRET_KEY"] = "csrf-test-access-key-not-for-production"
os.environ["SECRET_REFRESH_KEY"] = (
    "csrf-test-refresh-key-not-for-production"
)

from fastapi.testclient import TestClient
from main import app


class TestCsrfIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Fail immediately if a test unexpectedly tries to connect
        # to the database.
        cls.db_guard = patch(
            "sqlalchemy.engine.Engine.connect",
            side_effect=AssertionError(
                "Database connections are forbidden in CSRF tests"
            ),
        )
        cls.db_guard.start()
        cls.addClassCleanup(cls.db_guard.stop)

        cls.client = TestClient(app, follow_redirects=False)
        cls.addClassCleanup(cls.client.close)

    def test_finance_post_without_header_returns_403(self):
        response = self.client.post(
            "/api/finance/transactions",
            json={},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "csrf_validation_failed",
        )

    def test_finance_delete_without_header_returns_403(self):
        response = self.client.delete(
            "/api/finance/transactions/999999",
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_site_request_returns_403(self):
        response = self.client.post(
            "/api/finance/transactions",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Site": "cross-site",
            },
            json={},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_site_cross_origin_returns_403(self):
        response = self.client.post(
            "/api/finance/transactions",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Site": "same-site",
            },
            json={},
        )
        self.assertEqual(response.status_code, 403)

    def test_valid_header_reaches_authentication(self):
        response = self.client.delete(
            "/api/finance/transactions/999999",
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        # No login cookie: authentication should reject it,
        # not the CSRF middleware.
        self.assertEqual(response.status_code, 401)

    def test_finance_get_is_unaffected(self):
        response = self.client.get(
            "/api/finance/accounts",
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_without_header_returns_403(self):
        response = self.client.post("/auth/refresh")
        self.assertEqual(response.status_code, 403)

    def test_refresh_with_header_reaches_authentication(self):
        # Bypass rate limiting so Redis is never contacted.
        with patch(
            "modules.auth.router.is_rate_limited",
            return_value=False,
        ):
            response = self.client.post(
                "/auth/refresh",
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Sec-Fetch-Site": "same-origin",
                },
            )

        # No refresh cookie: session is expired.
        self.assertEqual(response.status_code, 401)

    def test_webhook_is_excluded_from_csrf_check(self):
        response = self.client.post(
            "/api/finance/imports/webhook-ingest",
            json={},
        )

        # Invalid body is rejected by FastAPI validation,
        # rather than the CSRF middleware.
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
