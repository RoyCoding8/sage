"""Contract checks between FastAPI routes and the handwritten TypeScript client."""

import re
from pathlib import Path

import api


def test_frontend_client_covers_every_product_api_route():
    """Every non-health backend operation has a matching frontend client call."""
    openapi = api.app.openapi()
    backend = {
        (method.upper(), path)
        for path, operations in openapi["paths"].items()
        if path.startswith("/api/") and not path.startswith("/api/health/")
        for method in operations
        if method.lower() in {"get", "post", "put", "delete", "patch"}
    }

    client_path = Path(__file__).parents[1] / "frontend" / "src" / "api" / "client.ts"
    source = client_path.read_text(encoding="utf-8")
    calls = re.findall(
        r"api\.(get|post|put|delete|patch)(?:<[^>]+>)?\(\s*([`'\"])(.+?)\2",
        source,
    )
    frontend = set()
    for method, _quote, path in calls:
        normalized = re.sub(
            r"\$\{(\w+)\}", lambda match: "{" + _snake(match.group(1)) + "}", path
        )
        frontend.add((method.upper(), f"/api{normalized}"))

    assert backend - frontend == set()


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
