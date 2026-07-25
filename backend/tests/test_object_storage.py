import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.object_storage import DisabledSessionObjectStore, S3SessionObjectStore


class _Paginator:
    def __init__(self, client) -> None:
        self.client = client

    def paginate(self, *, Bucket: str, Prefix: str):
        self.client.assert_bucket(Bucket)
        yield {
            "Contents": [
                {"Key": key}
                for key in sorted(self.client.objects)
                if key.startswith(Prefix)
            ]
        }


class _FakeS3Client:
    def __init__(self) -> None:
        self.bucket_exists = False
        self.objects: dict[str, bytes] = {}
        self.upload_count = 0

    def assert_bucket(self, bucket: str) -> None:
        if bucket != "test-bucket" or not self.bucket_exists:
            raise AssertionError("bucket is not ready")

    def head_bucket(self, *, Bucket: str) -> None:
        if Bucket != "test-bucket" or not self.bucket_exists:
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {"Code": "NoSuchBucket"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadBucket",
            )

    def create_bucket(self, *, Bucket: str) -> None:
        if Bucket != "test-bucket":
            raise AssertionError("unexpected bucket")
        self.bucket_exists = True

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        ExtraArgs: dict,
    ) -> None:
        self.assert_bucket(bucket)
        if "ContentType" not in ExtraArgs:
            raise AssertionError("content type is required")
        self.objects[key] = Path(filename).read_bytes()
        self.upload_count += 1

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.assert_bucket(bucket)
        Path(filename).write_bytes(self.objects[key])

    def get_paginator(self, name: str):
        if name != "list_objects_v2":
            raise AssertionError("unexpected paginator")
        return _Paginator(self)


class ObjectStorageTests(unittest.TestCase):
    def test_environment_configuration_is_all_or_nothing(self) -> None:
        names = {
            "MPOS_STORAGE_ENDPOINT": "",
            "MPOS_STORAGE_REGION": "",
            "MPOS_STORAGE_ACCESS_KEY_ID": "",
            "MPOS_STORAGE_SECRET_ACCESS_KEY": "",
            "MPOS_STORAGE_BUCKET": "",
            "MPOS_STORAGE_SESSION_TOKEN": "",
        }
        with patch.dict(os.environ, names):
            self.assertIsInstance(
                S3SessionObjectStore.from_env(),
                DisabledSessionObjectStore,
            )
        names["MPOS_STORAGE_ENDPOINT"] = "https://storage.example.test"
        with patch.dict(os.environ, names):
            with self.assertRaisesRegex(
                RuntimeError,
                "Incomplete object-storage configuration",
            ):
                S3SessionObjectStore.from_env()

    def test_session_token_is_forwarded_without_exposing_it(self) -> None:
        names = {
            "MPOS_STORAGE_ENDPOINT": "https://storage.example.test",
            "MPOS_STORAGE_REGION": "ap-southeast-1",
            "MPOS_STORAGE_ACCESS_KEY_ID": "project-ref",
            "MPOS_STORAGE_SECRET_ACCESS_KEY": "anon-key",
            "MPOS_STORAGE_BUCKET": "test-bucket",
            "MPOS_STORAGE_SESSION_TOKEN": "server-side-jwt",
        }
        with patch.dict(os.environ, names):
            with patch("app.object_storage.boto3.client") as create_client:
                store = S3SessionObjectStore.from_env()
        self.assertIsInstance(store, S3SessionObjectStore)
        kwargs = create_client.call_args.kwargs
        self.assertEqual(kwargs["aws_session_token"], "server-side-jwt")
        self.assertEqual(kwargs["aws_access_key_id"], "project-ref")
        self.assertNotIn("server-side-jwt", repr(store.__dict__))

    def test_session_round_trip_and_unchanged_file_cache(self) -> None:
        client = _FakeS3Client()
        store = S3SessionObjectStore(client, "test-bucket")
        session_id = "sess_0123456789abcdef"
        with tempfile.TemporaryDirectory() as source_temp:
            source_root = Path(source_temp)
            state = source_root / session_id / "session_state.json"
            artifact = source_root / session_id / "artifacts" / "app.mpk"
            state.parent.mkdir(parents=True)
            artifact.parent.mkdir(parents=True)
            state.write_text('{"status":"completed"}', encoding="utf-8")
            artifact.write_bytes(b"mpk-data")

            store.sync_session(source_root, session_id)
            self.assertEqual(client.upload_count, 2)
            store.sync_session(source_root, session_id)
            self.assertEqual(client.upload_count, 2)

        client.objects["sessions/not-a-session/../../unsafe"] = b"unsafe"
        with tempfile.TemporaryDirectory() as restored_temp:
            restored_root = Path(restored_temp)
            store.restore_all(restored_root)
            self.assertEqual(
                (restored_root / session_id / "session_state.json").read_text(
                    encoding="utf-8"
                ),
                '{"status":"completed"}',
            )
            self.assertEqual(
                (restored_root / session_id / "artifacts" / "app.mpk").read_bytes(),
                b"mpk-data",
            )
            self.assertFalse((restored_root / "unsafe").exists())


if __name__ == "__main__":
    unittest.main()
