"""Rules that keep students safe with each other: age bands and parental consent.

Students only debate others in their own age band (11-13, 14-17, 18+), and under-18s need a parent's
consent (an emailed code) before any live debate with another student.
"""

from datetime import date

from app.models import ConsentStatus, Profile

BANDS = ("11-13", "14-17", "18+")


def age_of(profile: Profile) -> int | None:
    return date.today().year - profile.birth_year if profile.birth_year else None


def age_band(profile: Profile) -> str | None:
    age = age_of(profile)
    if age is None:
        return None
    if age >= 18:
        return "18+"
    return "14-17" if age >= 14 else "11-13"


def birth_years(band: str) -> tuple[int, int]:
    """The birth years (oldest first) of everyone in a band this year."""
    year = date.today().year
    youngest, oldest = {"11-13": (0, 13), "14-17": (14, 17), "18+": (18, 120)}[band]
    return year - oldest, year - youngest


def needs_consent(profile: Profile) -> bool:
    age = age_of(profile)
    return age is not None and age < 18


def live_block_reason(profile: Profile) -> tuple[str, str] | None:
    """Why this student can't take part in live debates yet (message, code), or None."""
    if age_of(profile) is None:
        return "Add your age in your profile to use live debates.", "age_required"
    if needs_consent(profile) and profile.consent_status != ConsentStatus.GRANTED:
        return "Ask a parent to confirm before you debate other students.", "consent_required"
    return None


def same_band(first: Profile, second: Profile) -> bool:
    band = age_band(first)
    return band is not None and band == age_band(second)
