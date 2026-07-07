"""S3-compatible storage adapter (prod). boto3 in a worker thread + resilience.

boto3's low-level client is thread-safe, so one client is shared and each call
is offloaded via ``asyncio.to_thread``. Every call is wrapped in
``call_resilient`` (per-attempt timeout, bounded retry, circuit breaker) —
botocore errors are surfaced as ``StorageError`` (retryable). boto3 is imported
here only, so a filesystem-only deployment never loads it.
"""

import asyncio
from typing import ClassVar

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from core.files.ports import StorageError
from shared.config import Settings
from shared.resilience import CircuitBreaker, RetryPolicy, call_resilient

_TIMEOUT_SECONDS = 10.0


class S3Storage:
    backend: ClassVar[str] = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        if not bucket or not access_key or not secret_key:
            raise StorageError("S3 storage requires a bucket, access key and secret key")
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # We do our own retries/backoff; disable botocore's so they don't stack.
            config=Config(
                connect_timeout=_TIMEOUT_SECONDS,
                read_timeout=_TIMEOUT_SECONDS,
                retries={"max_attempts": 0},
            ),
        )
        self._breaker = CircuitBreaker(name="s3", failure_threshold=5, recovery_time=30.0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Storage":
        return cls(
            bucket=settings.files_s3_bucket,
            endpoint_url=settings.files_s3_endpoint_url,
            region=settings.files_s3_region,
            access_key=settings.files_s3_access_key,
            secret_key=settings.files_s3_secret_key,
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        async def _op() -> None:
            try:
                await asyncio.to_thread(
                    self._client.put_object,
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
            except (BotoCoreError, ClientError) as exc:
                raise StorageError(f"s3 put_object failed: {exc}") from exc

        await call_resilient(
            _op,
            timeout=_TIMEOUT_SECONDS,
            retry=RetryPolicy(attempts=3),
            breaker=self._breaker,
            error_cls=StorageError,
        )

    async def get(self, key: str) -> bytes:
        async def _op() -> bytes:
            try:
                response = await asyncio.to_thread(
                    self._client.get_object, Bucket=self._bucket, Key=key
                )
                body = await asyncio.to_thread(response["Body"].read)
            except (BotoCoreError, ClientError) as exc:
                raise StorageError(f"s3 get_object failed: {exc}") from exc
            return bytes(body)

        return await call_resilient(
            _op,
            timeout=_TIMEOUT_SECONDS,
            retry=RetryPolicy(attempts=3),
            breaker=self._breaker,
            error_cls=StorageError,
        )

    async def delete(self, key: str) -> None:
        async def _op() -> None:
            try:
                await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
            except (BotoCoreError, ClientError) as exc:
                raise StorageError(f"s3 delete_object failed: {exc}") from exc

        await call_resilient(
            _op,
            timeout=_TIMEOUT_SECONDS,
            retry=RetryPolicy(attempts=3),
            breaker=self._breaker,
            error_cls=StorageError,
        )
