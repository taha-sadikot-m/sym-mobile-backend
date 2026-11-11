"""The comfort-language rule (spec 3a): students practise speaking English; explanations come in the
language they're most comfortable in.

Every student-facing prompt adds `rule(language)` to its system instruction. Explanations, tips,
encouragement and the meaning of feedback are in the comfort language; every sentence the student must
say and every better version stays in English, with its meaning in the comfort language (in brackets, or
in the answer's `meaning` field when it has one).
"""

from typing import Literal, cast, get_args

ComfortLanguage = Literal["en", "ta", "ta_latn", "hi", "hi_latn"]
LANGUAGES: tuple[str, ...] = get_args(ComfortLanguage)

NAMES = {
    "en": "English",
    "ta": "Tamil (Tamil script)",
    "ta_latn": "Tanglish (Tamil in English letters)",
    "hi": "Hindi (Devanagari script)",
    "hi_latn": "Hinglish (Hindi in English letters)",
}

_SPOKEN = {"ta": "Tamil", "ta_latn": "Tamil", "hi": "Hindi", "hi_latn": "Hindi"}

_WRITTEN = {
    "ta": "simple spoken Tamil, written in Tamil script",
    "ta_latn": (
        "simple spoken Tamil written in English letters, the way students type Tamil in chats "
        "(Tanglish, e.g. “Nee nalla pesina, aana konjam slow-a pesu”), never in Tamil script"
    ),
    "hi": "simple spoken Hindi, written in Devanagari script",
    "hi_latn": (
        "simple spoken Hindi written in English letters, the way students type Hindi in chats "
        "(Hinglish, e.g. “Tumne accha bola, bas thoda dheere bolo”), never in Devanagari"
    ),
}

MEANING = "The meaning of the English line in the student's comfort language; empty when that is English."


def normalise(value: str | None) -> ComfortLanguage:
    """A stored or requested value as a known language; anything unknown is English."""
    return cast(ComfortLanguage, value) if value in LANGUAGES else "en"


def rule(language: str) -> str:
    """The prompt instruction for a student whose comfort language is `language`."""
    language = normalise(language)
    if language == "en":
        return (
            "Language: the student practises speaking English. Write explanations, tips, encouragement and "
            "feedback in plain, simple English that an Indian school student understands easily. Every "
            "sentence the student must say, and every improved or better version, is in English. Leave every "
            "'meaning' field empty."
        )
    spoken = _SPOKEN[language]
    return (
        f"Language: the student practises speaking English and is most comfortable in {spoken}. Write "
        f"explanations, tips, encouragement and the meaning of feedback in {_WRITTEN[language]}. Every "
        "sentence the student must say, every sample line and every improved or better version stays in "
        f"English, followed by its {spoken} meaning in brackets; where the answer has a 'meaning' field, put "
        f"the {spoken} meaning there instead of in brackets. Quotes of what the student said stay exactly as "
        "they said them. Keep debate and speaking terms (such as rebuttal or filler words) in English when "
        "that is clearer. Never add an English translation of your own explanations, tips or meanings: only "
        "the English lines the student should say are in English. Never ask the student to speak in "
        f"{spoken}: they always speak in English."
    )


def meaning(language: str, text: str | None) -> str | None:
    """A model's 'meaning' field as the app gets it: None for English students or when it's blank."""
    if normalise(language) == "en" or not text or not text.strip():
        return None
    return text.strip()


def log_label(*languages: str) -> str:
    """What `ai_calls.language` records for a call written for one or more students (e.g. 'ta+hi')."""
    unique = list(dict.fromkeys(normalise(language) for language in languages))
    return "+".join(unique)
