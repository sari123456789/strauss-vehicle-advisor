"""The committed Business API OpenAPI document matches the code (drift check, Step 4D)."""

import json

from tests.contract.business_openapi import OPENAPI_PATH, rendered


def test_business_openapi_has_no_drift() -> None:
    assert OPENAPI_PATH.is_file(), "run: python -m tests.contract.business_openapi"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == rendered()


def test_every_case_route_is_bearer_protected_and_documents_problems() -> None:
    document = json.loads(rendered())
    public = {
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/refresh", "post"),
        ("/api/v1/auth/logout", "post"),
    }
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            if (path, method) in public:
                continue
            assert operation.get("security") == [{"HTTPBearer": []}], (path, method)
    assert "/api/v1/conversations" not in document["paths"]  # AI-owned, not here
    assert not [
        p for p in document["paths"] if p.startswith("/api/v1/cases") and p.endswith("/cases")
    ]  # no case creation route
