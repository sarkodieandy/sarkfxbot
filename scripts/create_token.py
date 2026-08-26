"""Create a short-lived local JWT for development; production uses an identity provider."""

from __future__ import annotations

import argparse

from app.api.auth import AuthService, Role
from app.config.settings import get_settings
from app.domain.enums import TradingEnvironment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, help="Local development subject identifier")
    parser.add_argument("--role", choices=[role.value for role in Role], default=Role.VIEWER.value)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if settings.app_env is TradingEnvironment.PRODUCTION:
        raise SystemExit("Local token creation is disabled in APP_ENV=production")
    token = AuthService(settings).create_access_token(args.subject, Role(args.role))
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
