"""Durable session mirroring through an S3-compatible object store."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


SESSION_ID_RE = re.compile(r"^sess_[a-f0-9]{16}$")


class DisabledSessionObjectStore:
    enabled = False

    def restore_all(self, session_root: Path) -> None:
        return None

    def sync_session(self, session_root: Path, session_id: str) -> None:
        return None

    def sync_path(self, session_root: Path, session_id: str, path: Path) -> None:
        return None


class S3SessionObjectStore:
    enabled = True

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket
        self._fingerprints: dict[str, tuple[int, int]] = {}
        self._ensure_bucket()

    @classmethod
    def from_env(cls) -> DisabledSessionObjectStore | S3SessionObjectStore:
        names = {
            "endpoint_url": "MPOS_STORAGE_ENDPOINT",
            "region_name": "MPOS_STORAGE_REGION",
            "aws_access_key_id": "MPOS_STORAGE_ACCESS_KEY_ID",
            "aws_secret_access_key": "MPOS_STORAGE_SECRET_ACCESS_KEY",
            "bucket": "MPOS_STORAGE_BUCKET",
        }
        values = {key: os.getenv(name, "").strip() for key, name in names.items()}
        if not any(values.values()):
            return DisabledSessionObjectStore()
        missing = [names[key] for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                "Incomplete object-storage configuration: " + ", ".join(missing)
            )
        bucket = values.pop("bucket")
        client = boto3.client(
            "s3",
            **values,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        return cls(client, bucket)

    def restore_all(self, session_root: Path) -> None:
        session_root.mkdir(parents=True, exist_ok=True)
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix="sessions/"):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                target = self._target_for_key(session_root, key)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                self.client.download_file(self.bucket, key, str(target))
                self._remember(key, target)

    def sync_session(self, session_root: Path, session_id: str) -> None:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("Invalid session ID for object storage")
        root = (session_root / session_id).resolve()
        if not root.is_dir() or session_root.resolve() not in root.parents:
            return
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                self.sync_path(session_root, session_id, path)

    def sync_path(self, session_root: Path, session_id: str, path: Path) -> None:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("Invalid session ID for object storage")
        root = (session_root / session_id).resolve()
        resolved = path.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise ValueError("Object-storage path escaped its session root")
        relative = resolved.relative_to(root).as_posix()
        key = f"sessions/{session_id}/{relative}"
        stat = resolved.stat()
        fingerprint = (stat.st_size, stat.st_mtime_ns)
        if self._fingerprints.get(key) == fingerprint:
            return
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(resolved),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        self._fingerprints[key] = fingerprint

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if status not in {404} and code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def _target_for_key(self, session_root: Path, key: str) -> Path | None:
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != "sessions":
            return None
        session_id = parts[1]
        if not SESSION_ID_RE.fullmatch(session_id):
            return None
        relative = Path(*parts[2:])
        if relative.is_absolute() or ".." in relative.parts:
            return None
        root = (session_root / session_id).resolve()
        target = (root / relative).resolve()
        if root not in target.parents:
            return None
        return target

    def _remember(self, key: str, path: Path) -> None:
        stat = path.stat()
        self._fingerprints[key] = (stat.st_size, stat.st_mtime_ns)


session_object_store = S3SessionObjectStore.from_env()
