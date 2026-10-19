"""Object storage over the S3 API only (ADR-015): SeaweedFS in Compose, any S3 in production.

boto3 is synchronous; calls run in worker threads. Keys are always prefixed with the project
id, so a key can never point into another project's files.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import IO, Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from forge.core.settings import Settings

CHUNK = 1024 * 1024


def document_key(project_id: uuid.UUID, collection_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"projects/{project_id}/collections/{collection_id}/documents/{document_id}"


def parsed_key(project_id: uuid.UUID, sha256_hex: str) -> str:
    """Parse results are shared by identical files of a project (dedup by SHA-256)."""
    return f"projects/{project_id}/parsed/{sha256_hex}.json"


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=(
                settings.s3_access_key.get_secret_value() if settings.s3_access_key else None
            ),
            aws_secret_access_key=(
                settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None
            ),
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    async def ensure_bucket(self) -> None:
        def go() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(go)

    async def put_file(self, key: str, fh: IO[bytes], content_type: str) -> None:
        def go() -> None:
            fh.seek(0)
            self._client.upload_fileobj(
                fh, self.bucket, key, ExtraArgs={"ContentType": content_type}
            )

        await asyncio.to_thread(go)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get_bytes(self, key: str) -> bytes:
        def go() -> bytes:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
            data: bytes = obj["Body"].read()
            return data

        return await asyncio.to_thread(go)

    async def exists(self, key: str) -> bool:
        def go() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError:
                return False

        return await asyncio.to_thread(go)

    async def open_stream(self, key: str) -> Iterator[bytes]:
        """A blocking iterator over the object; FastAPI runs it in a thread pool."""

        def go() -> Any:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"]

        body = await asyncio.to_thread(go)
        return iter(lambda: body.read(CHUNK), b"")

    async def download_to(self, key: str, path: str) -> None:
        await asyncio.to_thread(self._client.download_file, self.bucket, key, path)

    async def delete(self, keys: list[str]) -> None:
        def go() -> None:
            for i in range(0, len(keys), 1000):
                batch = [{"Key": k} for k in keys[i : i + 1000]]
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})

        if keys:
            await asyncio.to_thread(go)
