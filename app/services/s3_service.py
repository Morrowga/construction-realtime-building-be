# app/services/s3_service.py
#
# LOCAL STORAGE MOCK — swap this file in when USE_LOCAL_STORAGE=true in .env
# Files are written to /tmp/construction_local_storage/ inside the container.
# The API returns file:// URLs for uploads and direct file paths for downloads.
# Pre-signed URLs return a localhost URL served by the /files/ static endpoint.
# Drop-in replacement — same function signatures as the real s3_service.py.
#
import asyncio
import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger("app.s3.local")

LOCAL_ROOT = Path(os.environ.get("LOCAL_STORAGE_PATH", "/tmp/construction_local_storage"))
LOCAL_ROOT.mkdir(parents=True, exist_ok=True)

_USE_LOCAL = str(os.environ.get("USE_LOCAL_STORAGE", "false")).lower() in ("true", "1", "yes")


def _local_path(key: str) -> Path:
    # Safely resolve key as a relative path under LOCAL_ROOT.
    p = (LOCAL_ROOT / key).resolve()
    if not str(p).startswith(str(LOCAL_ROOT)):
        raise ValueError(f"Unsafe key traversal attempted: {key}")
    return p


def _object_url(key: str) -> str:
    if _USE_LOCAL:
        return f"http://localhost:8000/files/{key}"
    return f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{key}"


# ---------------------------------------------------------------------------
# Sync primitives (safe inside Celery workers)
# ---------------------------------------------------------------------------


def upload_file_sync(file_bytes: bytes, key: str, content_type: str) -> str:
    if _USE_LOCAL:
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file_bytes)
        logger.debug("LOCAL upload: %s (%d bytes)", key, len(file_bytes))
        return _object_url(key)

    # Real S3 path (only reached when USE_LOCAL_STORAGE is not set).
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _get_s3_client().put_object(
            Bucket=settings.s3_bucket_name,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )
        return _object_url(key)
    except (ClientError, BotoCoreError) as exc:
        logger.error("S3 upload failed for key=%s: %s", key, exc)
        raise


def download_file_sync(key: str) -> bytes:
    if _USE_LOCAL:
        path = _local_path(key)
        if not path.exists():
            raise FileNotFoundError(f"LOCAL storage: file not found: {key}")
        data = path.read_bytes()
        logger.debug("LOCAL download: %s (%d bytes)", key, len(data))
        return data

    from botocore.exceptions import BotoCoreError, ClientError

    try:
        response = _get_s3_client().get_object(Bucket=settings.s3_bucket_name, Key=key)
        return response["Body"].read()
    except (ClientError, BotoCoreError) as exc:
        logger.error("S3 download failed for key=%s: %s", key, exc)
        raise


def generate_presigned_url_sync(key: str, expires: int = 3600) -> str:
    if _USE_LOCAL:
        # In local mode return a direct localhost URL — no expiry needed.
        url = f"http://localhost:8000/files/{key}"
        logger.debug("LOCAL presign: %s", url)
        return url

    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=expires,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("S3 presign failed for key=%s: %s", key, exc)
        raise


def delete_file_sync(key: str) -> None:
    if _USE_LOCAL:
        path = _local_path(key)
        if path.exists():
            path.unlink()
            logger.debug("LOCAL delete: %s", key)
        return

    from botocore.exceptions import BotoCoreError, ClientError

    try:
        _get_s3_client().delete_object(Bucket=settings.s3_bucket_name, Key=key)
    except (ClientError, BotoCoreError) as exc:
        logger.error("S3 delete failed for key=%s: %s", key, exc)
        raise


# ---------------------------------------------------------------------------
# Async wrappers (safe inside FastAPI async routes)
# ---------------------------------------------------------------------------


async def upload_file(file_bytes: bytes, key: str, content_type: str) -> str:
    return await asyncio.to_thread(upload_file_sync, file_bytes, key, content_type)


async def generate_presigned_url(key: str, expires: int = 3600) -> str:
    return await asyncio.to_thread(generate_presigned_url_sync, key, expires)


async def delete_file(key: str) -> None:
    await asyncio.to_thread(delete_file_sync, key)


# ---------------------------------------------------------------------------
# Lazy S3 client — only constructed when USE_LOCAL_STORAGE is false
# ---------------------------------------------------------------------------

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3
        from botocore.config import Config as BotoConfig

        kwargs: dict = {
            "region_name": settings.aws_region,
            "config": BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        }
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client