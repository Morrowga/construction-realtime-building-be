# app/routers/model_files.py
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.model_file import ModelFile, ModelSourceType, ParseStatus
from app.models.user import User
from app.schemas.model_file import ManualZoneMap, ModelFileOut
from app.services import s3_service
from app.utils.permissions import check_project_access, require_project_member
from app.workers.tasks import process_model_file

router = APIRouter(prefix="/api/v1/projects/{project_id}/model", tags=["model-files"])

MAX_MODEL_BYTES = 200 * 1024 * 1024  # 200 MB

_SOURCE_FOR_EXT = {
    ".ifc":  (ModelSourceType.ifc,    "application/octet-stream"),
    ".pdf":  (ModelSourceType.pdf,    "application/pdf"),
    ".glb":  (ModelSourceType.manual, "model/gltf-binary"),
    ".gltf": (ModelSourceType.manual, "model/gltf+json"),
}

# Extensions that are ready to use immediately — no Celery parse job needed
_NO_PARSE_EXTS = {".glb", ".gltf"}

FileType = Literal["skeleton", "envelope", "interior"]


async def _get_or_create_model_file(db: AsyncSession, project_id: uuid.UUID) -> ModelFile:
    result = await db.execute(select(ModelFile).where(ModelFile.project_id == project_id))
    model_file = result.scalar_one_or_none()
    if model_file is None:
        model_file = ModelFile(project_id=project_id)
        db.add(model_file)
        await db.flush()
    return model_file


@router.post("/upload", response_model=ModelFileOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_model_file(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    file_type: FileType = Form("skeleton"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelFile:
    """Admin uploads a model file for one of three viewer layers.

    file_type defaults to "skeleton" for backward compatibility — existing
    callers (seed.py, any client not yet updated) that don't send file_type
    at all get EXACTLY the old behavior: IFC/PDF/GLB/GLTF all accepted,
    IFC/PDF parsed asynchronously via Celery, GLB/GLTF stored immediately
    as the primary gltf_s3_key.

    file_type="envelope" or "interior" only accept GLB/GLTF — IFC/PDF
    parsing only ever made sense for the primary structural model, not
    the two additional viewer layers — and write to their own dedicated
    column instead of touching gltf_s3_key/parse_status/source_type at
    all, so uploading an envelope layer can never clobber the skeleton's
    parse state or vice versa.
    """
    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    filename = (file.filename or "").lower()
    ext = next((e for e in _SOURCE_FOR_EXT if filename.endswith(e)), None)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only .ifc, .pdf, .glb, and .gltf files are supported",
        )

    if file_type != "skeleton" and ext not in _NO_PARSE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{file_type} only supports .glb or .gltf files — IFC/PDF parsing is skeleton-only",
        )

    source_type, content_type = _SOURCE_FOR_EXT[ext]

    contents = await file.read()
    if len(contents) > MAX_MODEL_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Model file exceeds the 200MB size limit",
        )

    model_file = await _get_or_create_model_file(db, project_id)

    if file_type == "envelope":
        key = f"projects/{project_id}/model/envelope{ext}"
        await s3_service.upload_file(contents, key, content_type)
        model_file.envelope_s3_key = key
        await db.commit()
        await db.refresh(model_file)
        return model_file

    if file_type == "interior":
        key = f"projects/{project_id}/model/interior{ext}"
        await s3_service.upload_file(contents, key, content_type)
        model_file.interior_s3_key = key
        await db.commit()
        await db.refresh(model_file)
        return model_file

    # file_type == "skeleton" — original behavior, unchanged
    key = f"projects/{project_id}/model/original{ext}"
    await s3_service.upload_file(contents, key, content_type)

    model_file.original_s3_key = key
    model_file.source_type = source_type
    model_file.parse_error = None

    if ext in _NO_PARSE_EXTS:
        # GLB/GLTF — store directly as the renderable model, mark done immediately
        model_file.gltf_s3_key = key
        model_file.parse_status = ParseStatus.done
        model_file.zone_map = {}  # admin can fill via /zone-map or /manual endpoint
    else:
        # IFC/PDF — queue async parse job
        model_file.parse_status = ParseStatus.pending

    await db.commit()
    await db.refresh(model_file)

    if ext not in _NO_PARSE_EXTS:
        process_model_file.delay(str(model_file.id))

    return model_file


@router.get("", response_model=ModelFileOut)
async def get_model_file(
    project=Depends(require_project_member()),
    db: AsyncSession = Depends(get_db),
) -> ModelFileOut:
    """Get model file status plus pre-signed URLs for whichever of the
    three layers (skeleton/envelope/interior) have actually been uploaded.
    """
    result = await db.execute(select(ModelFile).where(ModelFile.project_id == project.id))
    model_file = result.scalar_one_or_none()
    if model_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No model file for this project"
        )

    out = ModelFileOut.model_validate(model_file)
    if model_file.gltf_s3_key:
        out.gltf_url = await s3_service.generate_presigned_url(model_file.gltf_s3_key)
    if model_file.envelope_s3_key:
        out.envelope_url = await s3_service.generate_presigned_url(model_file.envelope_s3_key)
    if model_file.interior_s3_key:
        out.interior_url = await s3_service.generate_presigned_url(model_file.interior_s3_key)
    return out


@router.post("/manual", response_model=ModelFileOut)
async def save_manual_zone_map(
    body: ManualZoneMap,
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ModelFile:
    """Save a manually-defined zone map without a file upload."""
    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    model_file = await _get_or_create_model_file(db, project_id)
    model_file.zone_map = body.zone_map
    model_file.source_type = ModelSourceType.manual
    model_file.parse_status = ParseStatus.done
    model_file.parse_error = None
    await db.commit()
    await db.refresh(model_file)
    return model_file


@router.get("/zone-map")
async def get_zone_map(
    project=Depends(require_project_member()),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the parsed zone_map for the admin to review or edit."""
    result = await db.execute(select(ModelFile).where(ModelFile.project_id == project.id))
    model_file = result.scalar_one_or_none()
    if model_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No model file for this project"
        )
    return {
        "parse_status": model_file.parse_status.value,
        "source_type": model_file.source_type.value if model_file.source_type else None,
        "zone_map": model_file.zone_map or {},
        "parse_error": model_file.parse_error,
    }