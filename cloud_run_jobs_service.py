import os

from google.api_core.exceptions import GoogleAPICallError
from google.cloud.run_v2 import JobsClient, RunJobRequest


def trigger_file_processing_job() -> str:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    region = os.environ.get("CLOUD_RUN_JOB_REGION")
    job_name = os.environ.get("CLOUD_RUN_JOB_NAME")

    if not project_id:
        raise RuntimeError("Missing GOOGLE_CLOUD_PROJECT or PROJECT_ID environment variable")
    if not region:
        raise RuntimeError("Missing CLOUD_RUN_JOB_REGION environment variable")
    if not job_name:
        raise RuntimeError("Missing CLOUD_RUN_JOB_NAME environment variable")

    client = JobsClient()

    if job_name.startswith("projects/"):
        full_job_name = job_name
    else:
        full_job_name = client.job_path(project=project_id, location=region, job=job_name)

    try:
        operation = client.run_job(request=RunJobRequest(name=full_job_name))
        metadata = operation.metadata
        return getattr(metadata, "name", "")
    except GoogleAPICallError as exc:
        raise RuntimeError(f"Failed to trigger Cloud Run job '{full_job_name}': {exc}") from exc
