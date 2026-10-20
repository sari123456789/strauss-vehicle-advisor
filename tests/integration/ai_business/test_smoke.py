"""M4: `scripts/smoke.py` runs green against the real application (ASGI, scripted model).

The same script is used manually against a local `uvicorn` with Gemini and, later, against
the cloud URL. Here only the model is scripted; everything else is the production code path.
"""

from collections.abc import Iterator

import pytest
from scripts.smoke import Smoke

from app.ai.workflow.models import Intent, OptionReference
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation
from tests.integration.ai_business.app_support import (
    PASSWORD,
    application,
    application_database,
    running,
)

pytestmark = pytest.mark.sources


@pytest.fixture(scope="module")
def app_db() -> Iterator[str]:
    with application_database() as url:
        yield url


async def test_smoke_script_passes_every_step(app_db: str) -> None:
    llm = ScriptedLlm().interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
        )
    )
    app = application(app_db, llm=llm, worker_in_process=True)
    async with running(app) as client:
        smoke = Smoke(
            client, {"e001": PASSWORD, "e005": PASSWORD, "rep01": PASSWORD}, poll_timeout=45
        )
        ok = await smoke.run()
        assert ok, smoke.report()
        assert [name for name, passed, _ in smoke.results if passed] == [
            "1 ready",
            "2 login e001",
            "3 conversation",
            "4 compare CAR-10 (E-001)",
            "4b compare waiver (E-005)",
            "5 question + message + reply",
            "6 representative answer + continuation",
        ]
        assert "Bearer" not in smoke.report() and PASSWORD not in smoke.report()
