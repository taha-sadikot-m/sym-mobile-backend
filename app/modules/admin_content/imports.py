from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.content.importer import ImportFileError, import_file
from app.core import audit
from app.modules.admin_auth.deps import DbSession, client_ip, require_admin_header
from app.modules.admin_content.common import ContentEditor

router = APIRouter(
    prefix="/admin/api/imports", tags=["admin imports"], dependencies=[Depends(require_admin_header)]
)

MAX_BYTES = 5 * 1024 * 1024


class CountsOut(BaseModel):
    created: int
    updated: int
    unchanged: int


class ImportOut(BaseModel):
    applied: bool
    committees: CountsOut
    agendas: CountsOut
    problems: list[str]


@router.post("")
async def import_content(
    file: UploadFile, admin: ContentEditor, request: Request, session: DbSession, apply: bool = False
) -> ImportOut:
    """Preview (default) or apply a catalogue .json or agendas .csv. A preview never changes anything."""
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is larger than 5 MB.")
    try:
        result = await import_file(session, file.filename or "", raw)
    except ImportFileError as error:
        await session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    out = ImportOut(
        applied=apply,
        committees=CountsOut(**vars(result.committees)),
        agendas=CountsOut(**vars(result.agendas)),
        problems=result.problems,
    )
    if apply:
        audit.record(
            session,
            "content.imported",
            admin_id=admin.id,
            target_type="import",
            target_id=file.filename,
            ip=client_ip(request),
            committees=vars(result.committees),
            agendas=vars(result.agendas),
            problems=len(result.problems),
        )
        await session.commit()
    else:
        await session.rollback()
    return out
