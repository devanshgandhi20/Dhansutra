
import os
import unittest

from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Isolated test configuration, before importing the app.
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
from sqlalchemy.engine import Engine

from main import app
from modules.auth.userverification import get_db


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return

        attributes = dict(attrs)

        if attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


class TestAuthFormCsrf(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # No test may connect to a real database.
        cls.db_guard = patch.object(
            Engine,
            "connect",
            side_effect=AssertionError(
                "Database connections are forbidden in CSRF tests"
            ),
        )
        cls.db_guard.start()
        cls.addClassCleanup(cls.db_guard.stop)

    def setUp(self):
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.first.return_value = None

        # Override the application's actual FastAPI DB dependency.
        app.dependency_overrides[get_db] = lambda: self.db

        # Separate browser cookie jar for each test.
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.pop(get_db, None)

    def get_token(self, path="/login", client=None):
        client = client or self.client

        response = client.get(path)
        self.assertEqual(response.status_code, 200)

        parser = CsrfTokenParser()
        parser.feed(response.text)

        self.assertIsNotNone(
            parser.token,
            "Hidden csrf_token input was not found"
        )

        return parser.token

    def submit(self, path, token=None, password="TestPassword123!"):
        data = {
            "username": "csrf-test@example.invalid",
            "password": password,
        }

        if token is not None:
            data["csrf_token"] = token

        return self.client.post(path, data=data)

    def assert_csrf_blocked(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "csrf_validation_failed"},
        )

    # 1
    def test_login_page_generates_token(self):
        token = self.get_token("/login")
        self.assertGreaterEqual(len(token), 40)

    # 2
    def test_signup_reuses_same_session_token(self):
        login_token = self.get_token("/login")
        signup_token = self.get_token("/signup")

        self.assertEqual(login_token, signup_token)

    # 3
    def test_login_without_token_is_blocked(self):
        self.get_token("/login")

        response = self.submit("/login")
        self.assert_csrf_blocked(response)

    # 4
    def test_login_with_invalid_token_is_blocked(self):
        self.get_token("/login")

        response = self.submit("/login", "invalid-token")
        self.assert_csrf_blocked(response)

    # 5
    def test_signup_without_token_is_blocked(self):
        self.get_token("/signup")

        response = self.submit("/create_user")
        self.assert_csrf_blocked(response)

    # 6
    def test_signup_with_invalid_token_is_blocked(self):
        self.get_token("/signup")

        response = self.submit("/create_user", "invalid-token")
        self.assert_csrf_blocked(response)

    # 7
    def test_token_from_another_browser_is_rejected(self):
        first_token = self.get_token("/login")

        with TestClient(app, follow_redirects=False) as other:
            other_token = self.get_token("/login", client=other)

            self.assertNotEqual(first_token, other_token)

            response = other.post(
                "/login",
                data={
                    "username": "csrf-test@example.invalid",
                    "password": "TestPassword123!",
                    "csrf_token": first_token,
                },
            )

            self.assert_csrf_blocked(response)

    # 8
    def test_valid_login_token_reaches_authentication(self):
        token = self.get_token("/login")

        with patch("main.check_rate_limit", return_value=None):
            response = self.submit("/login", token)

        # Dummy user does not exist.
        # The request must reach normal authentication.
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "/login?error=1",
            response.headers["location"],
        )

        self.db.query.assert_called()

    # 9
    def test_valid_signup_token_reaches_validation(self):
        token = self.get_token("/signup")

        with patch("main.check_rate_limit", return_value=None):
            response = self.submit(
                "/create_user",
                token,
                password="short",
            )

        # CSRF passed; signup rejects the short password.
        self.assertEqual(response.status_code, 303)
        self.assertIn(
            "/signup?error=",
            response.headers["location"],
        )

    # 10
    def test_successful_login_invalidates_form_token(self):
        token = self.get_token("/login")

        fake_user = SimpleNamespace(
            id=123,
            username="csrf-test@example.invalid",
            password="fake-hash",
            is_active=True,
        )

        self.db.query.return_value.filter.return_value.first.return_value = (
            fake_user
        )

        with (
            patch("main.check_rate_limit", return_value=None),
            patch("main.verify_password", return_value=True),
            patch("main.start_session") as start_session,
        ):
            response = self.submit("/login", token)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/dashboard")
        start_session.assert_called_once()

        # Reusing the old token must fail.
        replay = self.submit("/login", token)
        self.assert_csrf_blocked(replay)

    # 11
    def test_successful_signup_invalidates_form_token(self):
        token = self.get_token("/signup")

        with (
            patch("main.check_rate_limit", return_value=None),
            patch("main.hash_password", return_value="fake-hash"),
        ):
            response = self.submit("/create_user", token)

        self.assertEqual(response.status_code, 303)
        self.assertIn(
            "/login?success=user_created",
            response.headers["location"],
        )

        self.db.add.assert_called_once()
        self.db.commit.assert_called_once()

        # Reusing the old token must fail.
        replay = self.submit("/create_user", token)
        self.assert_csrf_blocked(replay)


if __name__ == "__main__":
    unittest.main()