"""Set a demo account's password (operator CLI, Step 4D). The password is never printed.

Usage (from src/backend, after `alembic upgrade head` and `app.cli.seed_demo`):
    .venv\\Scripts\\python.exe -m app.cli.set_password --login e001
The password is read from STRAUSS_NEW_PASSWORD if set, otherwise from a hidden prompt.
Never put passwords in files under version control. Exit codes: 0 success, 1 failure.
"""

import argparse
import getpass
import json
import os

from app.bootstrap import build_business_persistence
from app.business.application.identity.credential_admin import CredentialAdminService
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.cli.import_data import _cli_actor
from app.runtime import run_async
from app.settings import get_settings

PASSWORD_ENV = "STRAUSS_NEW_PASSWORD"


async def _main(login: str, password: str) -> None:
    persistence = build_business_persistence(get_settings())
    try:
        service = CredentialAdminService(
            persistence.unit_of_work, ScryptPasswordHasher(), SystemClock()
        )
        await service.set_password(_cli_actor(), login, password)
    finally:
        await persistence.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set a demo account password (hashed).")
    parser.add_argument("--login", required=True)
    args = parser.parse_args(argv)
    password = os.environ.get(PASSWORD_ENV) or getpass.getpass("New password: ")
    try:
        run_async(_main(args.login, password))
    except Exception as error:  # report the stable code only; never the password
        code = getattr(error, "code", type(error).__name__)
        print(json.dumps({"status": "failed", "code": str(code)}))
        return 1
    print(json.dumps({"status": "password_set", "login": args.login}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
