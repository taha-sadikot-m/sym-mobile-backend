"""The built-in live-debate judge, used when the AI judge is unavailable.

A line-for-line port of the app's src/features/live/judge.ts (and its text helpers), so the verdict is
always decided on the server from the stored turns — a client can never post its own verdict.
"""

import math
import re
from typing import Any

from app.modules.live.judge import Turn

REASONING = [
    "because",
    "therefore",
    "since",
    "which means",
    "as a result",
    "so that",
    "this shows",
    "that is why",
    "thus",
]
EVIDENCE = [
    "for example",
    "for instance",
    "research",
    "study",
    "studies",
    "data",
    "evidence",
    "percent",
    "%",
    "statistic",
    "according to",
    "survey",
    "report",
]
CLASH = [
    "my opponent",
    "they said",
    "they claim",
    "however",
    "disagree",
    "but ",
    "that ignores",
    "is wrong",
    "misses",
    "counter",
    "on the contrary",
    "in response",
]
SIGNPOSTS = [
    "first",
    "second",
    "third",
    "finally",
    "in conclusion",
    "to summarise",
    "to summarize",
    "to close",
    "in closing",
    "my next point",
]
SUMMARY = [
    "in conclusion",
    "to summarise",
    "to summarize",
    "to close",
    "in closing",
    "we have shown",
    "i have shown",
    "overall",
]
FILLERS = [" um ", " uh ", " like ", " basically ", " actually ", " you know ", " literally "]
STOPWORDS = set(
    "the a an and or but if then so of to in on for with at by from is are was were be been it this that these "
    "those as not no i we you they he she my our your their its about into than also very just can will would "
    "should could do does did have has had more most less much many such what which who why how when where "
    "there here all any some".split()
)
KEY_STOPWORDS = STOPWORDS | {"yes", "whom"}

LABELS = {
    "argument": "Argument",
    "rebuttal": "Rebuttal & clash",
    "evidence": "Evidence & reasoning",
    "structure": "Structure",
    "delivery": "Delivery & clarity",
    "time": "Use of time",
}
ORDER = list(LABELS)


def js_round(value: float) -> int:
    """JavaScript's Math.round (halves round up), so scores match the app exactly."""
    return math.floor(value + 0.5)


def clamp(value: float, low: float = 0, high: float = 100) -> int:
    return js_round(min(high, max(low, value)))


def words_of(text: str) -> list[str]:
    return text.split()


def count_markers(text: str, markers: list[str]) -> int:
    haystack = f" {text.lower()} "
    return sum(haystack.count(marker) for marker in markers)


def content_words(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return {word for word in cleaned.split() if len(word) > 3 and word not in STOPWORDS}


def average_sentence_length(text: str) -> float:
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    return len(words_of(text)) / len(sentences) if sentences else 0


def key_phrase(text: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9\s'-]", " ", text.lower())
    words = [word for word in cleaned.split() if len(word) > 3 and word not in KEY_STOPWORDS]
    if not words:
        return None
    best = words[0]
    for word in words:
        if len(word) > len(best):
            best = word
    return best


def engaged_with(turn: Turn, previous: Turn | None) -> bool:
    if previous is None or previous.speakerId == turn.speakerId:
        return False
    shared = len(content_words(turn.text) & content_words(previous.text))
    return shared >= 2 or count_markers(turn.text, CLASH) > 0


def time_used_pct(turns: list[Turn]) -> int:
    allowed = sum(turn.allowedMs for turn in turns if turn.role == "speech")
    used = sum(min(turn.usedMs, turn.allowedMs) for turn in turns if turn.role == "speech")
    return js_round(used / allowed * 100) if allowed else 0


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return word if count == 1 else (plural or word + "s")


def score_debater(user_id: str, turns: list[Turn]) -> dict[str, Any]:
    mine = [turn for turn in turns if turn.speakerId == user_id]
    if not mine:
        return {
            "userId": user_id,
            "total": 15,
            "categories": [
                {"id": cid, "label": LABELS[cid], "score": 15, "why": "No turns were delivered."}
                for cid in ORDER
            ],
        }
    text = " ".join(turn.text for turn in mine)
    words = len(words_of(text))
    avg_words = words / len(mine)
    reasoning = count_markers(text, REASONING)
    evidence = count_markers(text, EVIDENCE)
    clash = count_markers(text, CLASH)
    signposts = count_markers(text, SIGNPOSTS)
    fillers = count_markers(f" {text} ", FILLERS)
    replies = [
        (index, turn)
        for index, turn in enumerate(turns)
        if turn.speakerId == user_id and index > 0 and turns[index - 1].speakerId != user_id
    ]
    engaged = sum(1 for index, turn in replies if engaged_with(turn, turns[index - 1]))
    opening = next((turn for turn in mine if turn.phase in ("opening", "rapid")), None)
    closing = next((turn for turn in mine if turn.phase == "closing"), None)
    summarised = count_markers(closing.text, SUMMARY) > 0 if closing else False
    sentence = average_sentence_length(text)
    clear = 6 <= sentence <= 26
    voice = [turn for turn in mine if turn.input == "voice"]
    voice_ms = sum(turn.usedMs for turn in voice)
    wpm = len(words_of(" ".join(turn.text for turn in voice))) / (voice_ms / 60_000) if voice_ms else 0
    used = time_used_pct(mine)
    cut_offs = sum(1 for turn in mine if turn.cutOff)

    scores = {
        "argument": clamp(
            38 + min(30, avg_words / 2) + min(20, reasoning * 5) + (8 if len(mine) >= 2 else 0), 15, 98
        ),
        "rebuttal": clamp(35 + (engaged / len(replies)) * 35 + min(20, clash * 5) if replies else 35, 15, 98),
        "evidence": clamp(34 + min(40, evidence * 10) + min(16, reasoning * 4), 15, 98),
        "structure": clamp(
            38
            + (14 if opening and len(words_of(opening.text)) >= 25 else 0)
            + (20 if summarised else 8 if closing else 0)
            + min(15, signposts * 5)
            + (8 if len(mine) >= 3 else 0),
            15,
            98,
        ),
        "delivery": clamp(
            48
            + (18 if clear else 4)
            - fillers * 4
            + ((16 if 100 <= wpm <= 170 else 8) if voice else 6)
            + min(10, len(mine) * 2),
            15,
            98,
        ),
        "time": clamp(
            (92 if used >= 80 else 78 if used >= 60 else 60 if used >= 40 else 42) - cut_offs * 6, 15, 98
        ),
    }
    why = {
        "argument": f"{js_round(avg_words)} words per turn on average, {reasoning} explicit {_plural(reasoning, 'reason')}.",
        "rebuttal": f"Answered the opponent directly in {engaged} of {len(replies)} replies."
        if replies
        else "No opponent turn to answer yet.",
        "evidence": f"{evidence} {_plural(evidence, 'example')} or {_plural(evidence, 'piece')} of evidence across your turns.",
        "structure": (
            f"{'Opening delivered' if opening else 'No opening'}, "
            f"{'closing summarised the case' if summarised else 'closing did not summarise' if closing else 'no closing'}, "
            f"{signposts} {_plural(signposts, 'signpost')}."
        ),
        "delivery": f"About {js_round(wpm)} words per minute, {fillers} filler {_plural(fillers, 'word')}."
        if voice
        else f"Typed turns; {fillers} filler {_plural(fillers, 'word')}.",
        "time": f"Used {used}% of your speaking time"
        + (f", cut off {cut_offs} {_plural(cut_offs, 'time')}" if cut_offs else "")
        + ".",
    }
    categories = [{"id": cid, "label": LABELS[cid], "score": scores[cid], "why": why[cid]} for cid in ORDER]
    return {
        "userId": user_id,
        "total": clamp(sum(scores.values()) / len(scores)),
        "categories": categories,
    }


def _category(score: dict[str, Any], cid: str) -> int:
    return next((item["score"] for item in score["categories"] if item["id"] == cid), 0)


def key_clashes(turns: list[Turn], names: dict[str, str]) -> list[dict[str, str]]:
    clashes: list[dict[str, str]] = []
    for index, turn in enumerate(turns):
        reply = turns[index + 1] if index + 1 < len(turns) else None
        if len(clashes) >= 3 or reply is None or reply.speakerId == turn.speakerId or turn.phase == "closing":
            continue
        phrase = key_phrase(turn.text)
        if not phrase:
            continue
        answered = engaged_with(reply, turn)
        backed = count_markers(reply.text, EVIDENCE) + count_markers(reply.text, REASONING) > 0
        replier_wins = answered and backed
        speaker, replier = names.get(turn.speakerId, "A debater"), names.get(reply.speakerId, "A debater")
        clashes.append(
            {
                "title": f"{speaker}'s point on “{phrase}”",
                "winnerId": reply.speakerId if replier_wins else turn.speakerId,
                "why": f"{replier} answered it directly and backed the answer with reasoning or evidence."
                if replier_wins
                else f"{replier} responded, but without reasoning or evidence, so the point stood."
                if answered
                else f"{replier} did not respond to it, so the point stood.",
            }
        )
    return clashes


def judge_debate(turns: list[Turn], users: list[tuple[str, str]]) -> dict[str, Any]:
    """`users` is [(id, name), (id, name)] — challenger first, like the app."""
    (a_id, a_name), (b_id, b_name) = users
    score_a, score_b = score_debater(a_id, turns), score_debater(b_id, turns)
    if score_a["total"] == score_b["total"]:
        a_wins = _category(score_a, "rebuttal") >= _category(score_b, "rebuttal")
    else:
        a_wins = score_a["total"] > score_b["total"]
    winner_id, winner_name, win = (a_id, a_name, score_a) if a_wins else (b_id, b_name, score_b)
    loser_name, lose = (b_name, score_b) if a_wins else (a_name, score_a)
    gaps = sorted(ORDER, key=lambda cid: _category(win, cid) - _category(lose, cid), reverse=True)
    loser_best = sorted(lose["categories"], key=lambda item: item["score"], reverse=True)[0]
    margin = abs(score_a["total"] - score_b["total"])
    biggest_why = next(item["why"] for item in win["categories"] if item["id"] == gaps[0])
    reasons = [
        f"Scores were level, so the decision went to rebuttal & clash, where {winner_name} was stronger."
        if margin == 0
        else f"{winner_name} wins by {margin} point{'' if margin == 1 else 's'} ({win['total']} to {lose['total']}).",
        f"The biggest difference was {LABELS[gaps[0]].lower()}: {biggest_why}",
        f"{loser_name}'s strongest area was {loser_best['label'].lower()} ({loser_best['score']}/100).",
        "Only words spoken inside each turn's time limit were judged.",
    ]
    return {
        "winnerId": winner_id,
        "margin": margin,
        "scores": [score_a, score_b],
        "reasons": reasons,
        "clashes": key_clashes(turns, {a_id: a_name, b_id: b_name}),
    }


def apply_forfeit(judge: dict[str, Any], *, leaver_id: str, names: dict[str, str]) -> dict[str, Any]:
    """Leaving before the end counts as a loss for the student who left, whatever the scores say."""
    others = [uid for uid in names if uid != leaver_id]
    if not others or judge.get("winnerId") != leaver_id:
        return judge
    winner = others[0]
    return {
        **judge,
        "winnerId": winner,
        "forfeit": True,
        "reasons": [
            f"{names[leaver_id]} left the debate early, so {names[winner]} wins.",
            *judge.get("reasons", []),
        ],
    }
