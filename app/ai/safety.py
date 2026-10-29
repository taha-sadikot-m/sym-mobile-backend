"""Putting student-written text into prompts safely.

Prompts mark student text with tags such as <student>…</student>; a student who types "</student> Ignore
the rules…" could close the tag early and talk to the model as if they were us. `plain()` swaps angle
brackets for look-alike characters so student text can never open or close a tag, and caps its length.
"""

_SWAP = str.maketrans({"<": "‹", ">": "›"})


def plain(text: str | None, limit: int) -> str:
    return (text or "").strip()[:limit].translate(_SWAP)
