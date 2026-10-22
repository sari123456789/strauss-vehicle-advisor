"""The committed conversation API OpenAPI document matches the code (drift check, M4)."""

import json

from tests.contract.ai_openapi import OPENAPI_PATH, rendered

EXPECTED = {
    ("/api/v1/conversations", "post"),
    ("/api/v1/conversations", "get"),
    ("/api/v1/conversations/{conversationId}", "get"),
    ("/api/v1/conversations/{conversationId}", "delete"),
    ("/api/v1/conversations/{conversationId}/messages", "post"),
    ("/api/v1/conversations/{conversationId}/messages", "get"),
    ("/api/v1/conversations/{conversationId}/inputs/{inputId}", "get"),
}


def test_ai_openapi_has_no_drift() -> None:
    assert OPENAPI_PATH.is_file(), "run: python -m tests.contract.ai_openapi"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == rendered()


def test_conversation_routes_are_complete_and_bearer_protected() -> None:
    document = json.loads(rendered())
    found = {
        (path, method) for path, operations in document["paths"].items() for method in operations
    }
    assert found == EXPECTED
    for path, method in found:
        assert document["paths"][path][method].get("security") == [{"HTTPBearer": []}]
