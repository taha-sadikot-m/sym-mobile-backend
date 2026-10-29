"""Import committees and agendas from the SYM catalogue JSON or the agendas CSV.

Re-importing is safe: records are matched by slug, descriptive fields are refreshed from the file, and
anything an admin decides (status, order, seats) is left alone.

New records get a status by these rules:
  * committees where delegates represent a country are published; other formats (crisis portfolios,
    press, courts) start as drafts until the app supports those roles;
  * agendas from MyMUN are published; agendas with SYM-written caucus topics or frameworks start
    "in review", because that content is marked for review in the source file.
"""

import csv
import io
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agenda, Committee, ContentStatus, Difficulty, SeatType

SLUG_LENGTH = 60


class ImportFileError(ValueError):
    """The file can't be read as a catalogue; the message is shown to the admin."""


def slugify(text: str, length: int = SLUG_LENGTH) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:length]


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass
class ImportResult:
    committees: Counts = field(default_factory=Counts)
    agendas: Counts = field(default_factory=Counts)
    problems: list[str] = field(default_factory=list)
    new_committee_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class AgendaRow:
    slug: str
    title: str
    theme: str | None = None
    # None when the file doesn't say (the CSV); an existing agenda then keeps its value.
    kind: str | None = "admin"
    seen_at: str | None = None
    is_featured: bool = False
    caucus_topics: list[str] = field(default_factory=list)
    key_frameworks: list[str] = field(default_factory=list)

    @property
    def sym_written(self) -> bool:
        return bool(self.caucus_topics or self.key_frameworks)


@dataclass
class CommitteeRow:
    slug: str
    fields: dict[str, Any]
    agendas: list[AgendaRow]


def _merge_agendas(library: list[dict[str, Any]], featured: list[dict[str, Any]]) -> list[AgendaRow]:
    """The library and the featured list overlap; one agenda per slug, featured content added on top."""
    rows: dict[str, AgendaRow] = {}
    for item in library:
        slug = str(item.get("id") or slugify(str(item["title"])))
        rows[slug] = AgendaRow(
            slug=slug,
            title=str(item["title"]).strip(),
            theme=item.get("theme"),
            kind=item.get("kind", "mymun"),
            seen_at=item.get("seen_at"),
        )
    for item in featured:
        slug = str(item.get("id") or slugify(str(item["title"])))
        row = rows.setdefault(slug, AgendaRow(slug=slug, title=str(item["title"]).strip(), kind="sym"))
        row.is_featured = True
        row.caucus_topics = [str(topic) for topic in item.get("moderated_caucus_topics") or []]
        row.key_frameworks = [str(framework) for framework in item.get("key_frameworks") or []]
    return list(rows.values())


def parse_catalog_json(raw: bytes) -> list[CommitteeRow]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
        committees = data["committees"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ImportFileError(
            "This isn’t a SYM catalogue JSON file (expected a “committees” list)."
        ) from error
    rows: list[CommitteeRow] = []
    for item in committees:
        try:
            seat_type = SeatType(item.get("seat_type", "country"))
            difficulty = Difficulty(item.get("difficulty", "Beginner"))
            rows.append(
                CommitteeRow(
                    slug=str(item["id"]),
                    fields={
                        "abbr": str(item["abbr"]),
                        "name": str(item["name"]),
                        "category": str(item.get("category") or ""),
                        "seat_type": seat_type,
                        "difficulty": difficulty,
                        "description": str(item.get("real_body") or ""),
                        "decision_rule": str(item.get("decision_rule") or ""),
                        "binding": str(item.get("binding") or ""),
                        "typical_seats": item.get("typical_seats"),
                        "themes": item.get("themes") or [],
                        "mymun_upcoming_conferences": item.get("mymun_upcoming_conferences"),
                        "mymun_topic_count": item.get("mymun_topic_count"),
                    },
                    agendas=_merge_agendas(
                        item.get("agenda_library") or [], item.get("featured_agendas") or []
                    ),
                )
            )
        except (KeyError, ValueError) as error:
            raise ImportFileError(
                f"Committee “{item.get('id', '?')}” is incomplete or invalid: {error}"
            ) from error
    return rows


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def parse_agendas_csv(raw: bytes) -> dict[str, list[AgendaRow]]:
    """Agenda rows keyed by committee abbreviation (lower case). Committees must already exist."""
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError as error:
        raise ImportFileError("The CSV must be saved as UTF-8.") from error
    if not reader.fieldnames or not {"Committee", "Agenda"} <= set(reader.fieldnames):
        raise ImportFileError("The CSV needs at least “Committee” and “Agenda” columns.")
    library: dict[str, list[dict[str, Any]]] = {}
    featured: dict[str, list[dict[str, Any]]] = {}
    for line in reader:
        title = (line.get("Agenda") or "").strip()
        abbr = (line.get("Committee") or "").strip().lower()
        if not title or not abbr:
            continue
        slug = slugify(title)
        if (line.get("Source") or "").strip().lower() == "featured":
            featured.setdefault(abbr, []).append(
                {
                    "id": slug,
                    "title": title,
                    "moderated_caucus_topics": _split(line.get("Moderated caucus topics")),
                    "key_frameworks": _split(line.get("Key frameworks")),
                }
            )
        else:
            library.setdefault(abbr, []).append(
                {
                    "id": slug,
                    "title": title,
                    "theme": (line.get("Theme") or "").strip() or None,
                    "seen_at": (line.get("Seen at (conference)") or "").strip() or None,
                    "kind": None,
                }
            )
    return {
        abbr: _merge_agendas(library.get(abbr, []), featured.get(abbr, []))
        for abbr in sorted(set(library) | set(featured))
    }


def _changed(model: Any, values: dict[str, Any]) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(model, key) != value:
            setattr(model, key, value)
            changed = True
    return changed


async def _upsert_agendas(
    session: AsyncSession, committee: Committee, rows: list[AgendaRow], result: ImportResult
) -> None:
    existing = {
        agenda.slug: agenda
        for agenda in await session.scalars(select(Agenda).where(Agenda.committee_id == committee.id))
    }
    next_order = max((agenda.sort_order for agenda in existing.values()), default=-1) + 1
    for row in rows:
        values = {
            "title": row.title,
            "theme": row.theme,
            "seen_at": row.seen_at,
            "is_featured": row.is_featured,
            "caucus_topics": row.caucus_topics,
            "key_frameworks": row.key_frameworks,
        }
        agenda = existing.get(row.slug)
        if agenda is None:
            status = ContentStatus.IN_REVIEW if row.sym_written else ContentStatus.PUBLISHED
            agenda = Agenda(
                committee_id=committee.id,
                slug=row.slug,
                sort_order=next_order,
                status=status,
                kind=row.kind or "mymun",
                **values,
            )
            session.add(agenda)
            existing[row.slug] = agenda
            next_order += 1
            result.agendas.created += 1
        elif _changed(agenda, values if row.kind is None else {**values, "kind": row.kind}):
            result.agendas.updated += 1
        else:
            result.agendas.unchanged += 1


async def import_catalog(session: AsyncSession, rows: list[CommitteeRow]) -> ImportResult:
    """Apply a parsed catalogue in the current transaction (the caller commits or rolls back)."""
    result = ImportResult()
    existing = {committee.slug: committee for committee in await session.scalars(select(Committee))}
    next_order = max((committee.sort_order for committee in existing.values()), default=-1) + 1
    for row in rows:
        committee = existing.get(row.slug)
        if committee is None:
            status = (
                ContentStatus.PUBLISHED
                if row.fields["seat_type"] == SeatType.COUNTRY
                else ContentStatus.DRAFT
            )
            committee = Committee(slug=row.slug, sort_order=next_order, status=status, **row.fields)
            session.add(committee)
            await session.flush()
            next_order += 1
            result.committees.created += 1
            result.new_committee_ids.append(committee.id)
        elif _changed(committee, row.fields):
            result.committees.updated += 1
        else:
            result.committees.unchanged += 1
        await _upsert_agendas(session, committee, row.agendas, result)
    await session.flush()
    return result


async def import_agendas(session: AsyncSession, by_committee: dict[str, list[AgendaRow]]) -> ImportResult:
    result = ImportResult()
    committees = {committee.abbr.lower(): committee for committee in await session.scalars(select(Committee))}
    for abbr, rows in by_committee.items():
        committee = committees.get(abbr)
        if committee is None:
            result.problems.append(
                f"No committee with abbreviation “{abbr.upper()}” — {len(rows)} agenda(s) skipped."
            )
            continue
        await _upsert_agendas(session, committee, rows, result)
    await session.flush()
    return result


async def import_file(session: AsyncSession, filename: str, raw: bytes) -> ImportResult:
    name = filename.lower()
    if name.endswith(".json"):
        return await import_catalog(session, parse_catalog_json(raw))
    if name.endswith(".csv"):
        return await import_agendas(session, parse_agendas_csv(raw))
    raise ImportFileError("Upload the catalogue as .json or the agenda list as .csv.")
