substitutions:
  _REGION: asia-southeast1
  _SERVICE_NAME: tree-rag-service
  _JOB_NAME: tree-rag-job
  _AR_REPO: tree-rag
  _SERVICE_IMAGE_NAME: tree-rag-service
  _JOB_IMAGE_NAME: tree-rag-job
  _DEPLOY_SERVICE: "true"  # Set to true to always deploy
  _DEPLOY_JOB: "true"      # Set to true to always update job

steps:
  # 1. Build the Service Image (defaults to Uvicorn via Dockerfile CMD)
  - name: gcr.io/cloud-builders/docker
    id: Build service image
    args:
      - build
      - --file
      - Dockerfile
      - --build-arg
      - REQUIREMENTS_FILE=requirements-service.txt
      - --tag
      - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_SERVICE_IMAGE_NAME}:$COMMIT_SHA
      - --tag
      - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_SERVICE_IMAGE_NAME}:latest
      - .

  # 2. Build the Job Image (Installs LibreOffice + requirements-job.txt)
  - name: gcr.io/cloud-builders/docker
    id: Build job image
    args:
      - build
      - --file
      - Dockerfile
      - --build-arg
      - REQUIREMENTS_FILE=requirements-job.txt
      - --tag
      - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_JOB_IMAGE_NAME}:$COMMIT_SHA
      - --tag
      - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_JOB_IMAGE_NAME}:latest
      - .

  # 3. Deploy Cloud Run service
  - name: gcr.io/google.com/cloudsdktool/cloud-sdk
    id: Deploy Cloud Run service
    entrypoint: bash
    args:
      - -ceu
      - |
        if [[ "${_DEPLOY_SERVICE}" == "true" ]]; then
          gcloud run deploy "${_SERVICE_NAME}" \
            --region="${_REGION}" \
            --image="${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_SERVICE_IMAGE_NAME}:$COMMIT_SHA" \
            --platform=managed
        else
          echo "Skipping service deploy"
        fi

  # 4. Update Cloud Run job
  # This forces the container to run the python script instead of Uvicorn
  - name: gcr.io/google.com/cloudsdktool/cloud-sdk
    id: Update Cloud Run job
    entrypoint: bash
    args:
      - -ceu
      - |
        if [[ "${_DEPLOY_JOB}" == "true" ]]; then
          gcloud run jobs update "${_JOB_NAME}" \
            --region="${_REGION}" \
            --image="${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_JOB_IMAGE_NAME}:$COMMIT_SHA" \
            --clear-entrypoint \
            --command="python" \
            --args="job_runner.py"
        else
          echo "Skipping job update"
        fi

images:
  - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_SERVICE_IMAGE_NAME}:$COMMIT_SHA
  - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_SERVICE_IMAGE_NAME}:latest
  - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_JOB_IMAGE_NAME}:$COMMIT_SHA
  - ${_REGION}-docker.pkg.dev/$PROJECT_ID/${_AR_REPO}/${_JOB_IMAGE_NAME}:latest

options:
  logging: CLOUD_LOGGING_ONLY
