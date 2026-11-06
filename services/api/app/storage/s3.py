"""S3-compatible object storage (SeaweedFS locally)."""

from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings, get_settings


@dataclass
class ObjectInfo:
    size: int
    content_type: str
    etag: str


class Storage:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        common = dict(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **common)
        # Signing needs no network call, so this client only ever builds URLs for the browser.
        self._public = boto3.client("s3", endpoint_url=settings.s3_public_endpoint_url, **common)

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self.bucket)

    def presign_put(self, key: str, content_type: str, expires: int) -> str:
        return self._public.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
        )

    def presign_get(self, key: str, filename: str, content_type: str, expires: int) -> str:
        return self._public.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f"inline; filename*=UTF-8''{quote(filename)}",
            },
            ExpiresIn=expires,
        )

    def head(self, key: str) -> ObjectInfo | None:
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectInfo(
            size=resp["ContentLength"],
            content_type=resp.get("ContentType", ""),
            etag=resp.get("ETag", "").strip('"'),
        )

    def download(self, key: str, path) -> None:
        self._client.download_file(self.bucket, key, str(path))

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


@lru_cache
def get_storage() -> Storage:
    return Storage(get_settings())
