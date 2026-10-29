"""Create an admin console account (the first one must be created this way).

    uv run python -m scripts.create_admin --email you@example.com --name "Your Name" --role super_admin

The password is asked for interactively and never echoed. The authenticator app is set up at first sign-in.
"""

import argparse
import asyncio
import getpass
import sys

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from app.core import audit
from app.core.db import SessionLocal, engine
from app.core.passwords import hash_admin_password
from app.models import AdminRole, AdminUser

MIN_PASSWORD_LENGTH = 12


async def create(email: str, name: str, role: AdminRole, password: str) -> None:
    async with SessionLocal() as session:
        if await session.scalar(select(AdminUser).where(AdminUser.email == email)):
            raise SystemExit(f"An admin with email {email} already exists.")
        admin = AdminUser(email=email, full_name=name, role=role, password_hash=hash_admin_password(password))
        session.add(admin)
        await session.flush()
        audit.record(
            session, "admin.created", target_type="admin", target_id=str(admin.id), via="cli", role=role
        )
        await session.commit()
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--role", choices=[role.value for role in AdminRole], default=AdminRole.SUPER_ADMIN.value
    )
    args = parser.parse_args()

    try:
        # Same rules as the sign-in form, so every admin created here can actually sign in.
        email = validate_email(args.email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as error:
        sys.exit(f"Invalid email: {error}")

    password = getpass.getpass("Password (12+ characters): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords don't match.")

    asyncio.run(create(email, args.name.strip(), AdminRole(args.role), password))
    print(f"Created {args.role} {email}. Sign in at /admin to set up the authenticator app.")


if __name__ == "__main__":
    main()
