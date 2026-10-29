"""Password rules matching the web backend's Django validators (length, common, numeric, similarity),
so a password accepted in the app is also acceptable on the website."""

import re

MIN_LENGTH = 8

# The most common passwords; Django checks a 20,000-word list, these cover what students actually try.
COMMON = {
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "1234567890",
    "qwerty123",
    "qwertyuiop",
    "11111111",
    "00000000",
    "iloveyou",
    "abc12345",
    "abcd1234",
    "letmein1",
    "welcome1",
    "admin123",
    "india123",
    "football",
    "baseball",
    "sunshine",
    "princess",
    "dragon12",
    "monkey12",
    "passw0rd",
    "p@ssw0rd",
    "asdfghjk",
    "zxcvbnm1",
    "1q2w3e4r",
    "qwerty12",
    "superman",
    "pokemon1",
    "computer",
    "whatever",
    "trustno1",
}


def problems(password: str, *, email: str = "", name: str = "") -> str | None:
    """The first rule the password breaks, as a sentence for the student, or None if it's fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > 128:
        return "Use at most 128 characters."
    if password.isdigit():
        return "Use letters as well as numbers."
    if password.lower() in COMMON:
        return "That password is too common. Pick something harder to guess."
    lowered = password.lower()
    for part in [email.split("@")[0], *re.split(r"\s+", name)]:
        part = part.lower()
        if len(part) >= 4 and (part in lowered or lowered in part):
            return "Your password is too similar to your name or email."
    return None
