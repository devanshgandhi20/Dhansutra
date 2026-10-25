
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


# Extract the real middleware function without importing main.py.
# Importing main.py normally also initializes application dependencies.
MAIN = Path(__file__).resolve().parents[1] / "main.py"
tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))

methods = next(
    node for node in tree.body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "UNSAFE_METHODS"
        for target in node.targets
    )
)

middleware = next(
    node for node in tree.body
    if isinstance(node, ast.AsyncFunctionDef)
    and node.name == "csrf_protection"
)

# Avoid executing @app.middleware during the isolated unit test.
middleware.decorator_list = []


class FakeJSONResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


scope = {
    "Request": object,
    "JSONResponse": FakeJSONResponse,
}

test_module = ast.fix_missing_locations(
    ast.Module(body=[methods, middleware], type_ignores=[])
)

exec(compile(test_module, str(MAIN), "exec"), scope)

csrf_protection = scope["csrf_protection"]


class TestCsrfMiddleware(unittest.IsolatedAsyncioTestCase):

    async def assert_request(self, method, path, headers, blocked):
        request = SimpleNamespace(
            method=method,
            url=SimpleNamespace(path=path),
            headers=headers,
        )

        calls = 0
        success = object()

        async def call_next(_request):
            nonlocal calls
            calls += 1
            return success

        result = await csrf_protection(request, call_next)

        if blocked:
            self.assertIsInstance(result, FakeJSONResponse)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(
                result.content,
                {"detail": "csrf_validation_failed"}
            )
            self.assertEqual(calls, 0)
        else:
            self.assertIs(result, success)
            self.assertEqual(calls, 1)

    async def test_finance_writes_require_header(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                await self.assert_request(
                    method,
                    "/api/finance/transactions",
                    {},
                    blocked=True,
                )

    async def test_incorrect_header_is_rejected(self):
        await self.assert_request(
            "POST",
            "/api/finance/transactions",
            {"x-requested-with": "OtherClient"},
            blocked=True,
        )

    async def test_cross_origin_requests_are_rejected(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                await self.assert_request(
                    "POST",
                    "/api/finance/transactions",
                    {
                        "x-requested-with": "XMLHttpRequest",
                        "sec-fetch-site": site,
                    },
                    blocked=True,
                )

    async def test_legitimate_dashboard_requests_pass(self):
        headers = {
            "x-requested-with": "XMLHttpRequest",
            "sec-fetch-site": "same-origin",
        }

        for path in (
            "/api/finance/transactions",
            "/api/finance/imports/commit-receipts",
            "/auth/refresh",
        ):
            with self.subTest(path=path):
                await self.assert_request(
                    "POST", path, headers, blocked=False
                )

        # Browsers without Fetch Metadata support.
        await self.assert_request(
            "POST",
            "/api/finance/transactions",
            {"x-requested-with": "XMLHttpRequest"},
            blocked=False,
        )

    async def test_refresh_requires_header(self):
        await self.assert_request(
            "POST", "/auth/refresh", {}, blocked=True
        )

    async def test_excluded_routes_pass(self):
        for method, path in (
            ("GET", "/api/finance/transactions"),
            ("POST", "/api/finance/imports/webhook-ingest"),
            ("POST", "/login"),
            ("POST", "/create_user"),
            ("GET", "/auth/logout"),
        ):
            with self.subTest(path=path):
                await self.assert_request(
                    method, path, {}, blocked=False
                )


if __name__ == "__main__":
    unittest.main()
