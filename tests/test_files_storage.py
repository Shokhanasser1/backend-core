"""Storage adapters (core/files) in isolation — no DB, no app.

The filesystem adapter (dev/test default) is exercised directly; the S3 adapter is
exercised against an in-memory S3 (moto). Both honour the StoragePort contract:
put/get roundtrip, idempotent delete, and failures surface as StorageError.
"""

from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from core.files.adapters.filesystem import FilesystemStorage
from core.files.adapters.s3 import S3Storage
from core.files.ports import StorageError


async def test_filesystem_put_get_delete(tmp_path: Path) -> None:
    storage = FilesystemStorage(str(tmp_path))
    await storage.put("t/one", b"hello", content_type="image/png")
    assert await storage.get("t/one") == b"hello"
    await storage.delete("t/one")
    await storage.delete("t/one")  # idempotent — a second delete is a no-op


async def test_filesystem_get_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        await FilesystemStorage(str(tmp_path)).get("nope")


async def test_filesystem_rejects_traversal(tmp_path: Path) -> None:
    storage = FilesystemStorage(str(tmp_path))
    with pytest.raises(StorageError):
        await storage.put("../escape", b"x", content_type="image/png")


def test_s3_requires_credentials() -> None:
    with pytest.raises(StorageError):
        S3Storage(bucket="b", endpoint_url=None, region="us-east-1", access_key="", secret_key="")


async def test_s3_put_get_delete_roundtrip() -> None:
    with mock_aws():
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="k", aws_secret_access_key="s"
        )
        client.create_bucket(Bucket="bucket")
        storage = S3Storage(
            bucket="bucket",
            endpoint_url=None,
            region="us-east-1",
            access_key="k",
            secret_key="s",
        )
        await storage.put("t/one", b"payload", content_type="image/png")
        assert await storage.get("t/one") == b"payload"

        await storage.delete("t/one")
        # The object is gone (checked directly to avoid the adapter's retry loop).
        with pytest.raises(ClientError):
            client.head_object(Bucket="bucket", Key="t/one")
