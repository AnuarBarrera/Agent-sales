import logging
from typing import Any

from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class StorageProvider:
    """S3-compatible object storage provider."""

    def __init__(self):
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise RuntimeError("Storage requires boto3; install backend dependencies") from exc

        settings = get_settings()
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
            region_name=settings.storage_region,
        )
        self.bucket = settings.storage_bucket
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Ensure bucket exists."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404 or error.get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)
            logger.info("Created storage bucket: %s", self.bucket)

    def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Upload data to storage."""
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            return key
        except Exception as e:
            logger.error(f"Failed to upload {key}: {e}")
            raise

    def download(self, key: str) -> bytes:
        """Download data from storage."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as e:
            logger.error(f"Failed to download {key}: {e}")
            raise

    def delete(self, key: str) -> None:
        """Delete object from storage."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.error(f"Failed to delete {key}: {e}")
            raise

    def get_presigned_url(self, key: str, expiration: int = 3600) -> str:
        """Get presigned URL for object."""
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiration,
            )
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {key}: {e}")
            raise

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


class ReadOnlyStorageProvider:
    """S3-compatible reader for verification paths; construction never mutates storage."""

    def __init__(self):
        try:
            import boto3
            from botocore.client import Config
        except ImportError as exc:
            raise RuntimeError("Storage verification requires boto3") from exc

        settings = get_settings()
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
            region_name=settings.storage_region,
        )
        self.bucket = settings.storage_bucket

    def download(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()
