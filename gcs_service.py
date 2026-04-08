import os

from google.cloud import storage

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def get_storage_bucket() -> storage.Bucket:
    bucket_name = os.environ["GCS_BUCKET_NAME"]
    return _get_client().bucket(bucket_name)
