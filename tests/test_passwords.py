import django
from django.conf import settings

settings.configure(PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"])
django.setup()

from django.contrib.auth.hashers import check_password as django_check  # noqa: E402
from django.contrib.auth.hashers import make_password as django_make  # noqa: E402

from app.core.passwords import (  # noqa: E402
    DJANGO_ITERATIONS,
    check_admin_password,
    check_django_password,
    hash_admin_password,
    make_django_password,
)


def test_we_accept_passwords_set_on_the_website() -> None:
    encoded = django_make("Debate-2026!")
    assert check_django_password("Debate-2026!", encoded)
    assert not check_django_password("debate-2026!", encoded)


def test_the_website_accepts_passwords_set_in_the_app() -> None:
    encoded = make_django_password("Mun Delegate 7")
    assert encoded.startswith(f"pbkdf2_sha256${DJANGO_ITERATIONS}$")
    assert django_check("Mun Delegate 7", encoded)
    assert not django_check("Mun Delegate 8", encoded)


def test_unusable_or_foreign_hashes_never_match() -> None:
    assert not check_django_password("x", "!unusable")
    assert not check_django_password("x", "")
    assert not check_django_password("x", "argon2$argon2id$v=19$m=1,t=1,p=1$abc$def")
    assert not check_django_password("x", "pbkdf2_sha256$notanumber$salt$hash")


def test_admin_passwords_use_argon2() -> None:
    encoded = hash_admin_password("correct horse battery")
    assert encoded.startswith("$argon2id$")
    assert check_admin_password("correct horse battery", encoded)
    assert not check_admin_password("wrong", encoded)
    assert not check_admin_password("x", "not-a-hash")
