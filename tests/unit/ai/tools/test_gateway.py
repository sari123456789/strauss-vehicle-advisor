"""The workflow's tool gateway exposes every canonical Business tool."""

import inspect

from app.ai.tools.gateway import BusinessToolGateway
from app.contracts.business_port import CANONICAL_TOOL_NAMES, BusinessToolsPort


def test_gateway_exposes_every_canonical_tool() -> None:
    missing = [name for name in CANONICAL_TOOL_NAMES if not hasattr(BusinessToolGateway, name)]
    assert missing == []


def test_gateway_methods_take_the_port_request_without_a_tool_context() -> None:
    for name in CANONICAL_TOOL_NAMES:
        port_params = list(inspect.signature(getattr(BusinessToolsPort, name)).parameters)
        gateway_params = list(inspect.signature(getattr(BusinessToolGateway, name)).parameters)
        assert gateway_params == [p for p in port_params if p != "ctx"], name
