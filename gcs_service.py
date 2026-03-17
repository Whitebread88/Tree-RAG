import os

from google.cloud import storage


def get_storage_bucket() -> storage.Bucket:
    bucket_name = os.environ["GCS_BUCKET_NAME"]
    storage_client = storage.Client()
    return storage_client.bucket(bucket_name)
