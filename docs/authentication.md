# JWT authentication from chatbot-ui

All file, login and chat endpoints require an RS256 bearer JWT signed by the
chatbot-ui server. `auth.py` validates the public-key signature, exact issuer and
audience, required `sub`/`iss`/`aud`/`iat`/`exp` claims, and a maximum five-minute
token lifetime. Five seconds of clock tolerance are allowed. Missing or invalid
tokens return 401 with `WWW-Authenticate: Bearer`.

Existing email-based IDs are preserved exactly, including case. Existing `id`
and `user_id` request fields remain required for compatibility, but a mismatch
with the signed subject returns 403 before any business service executes. All
services receive the verified subject. Login also uses the signed name rather
than the request's name. Existing conversation ownership checks and document
filters remain in place. No database migration is needed.

## Configuration

Install `requirements-service.txt` and configure:

| Variable | Value |
| --- | --- |
| `RAG_JWT_PUBLIC_KEY` | Full RSA public PEM, at least 2048 bits |
| `RAG_JWT_ISSUER` | Exact value configured on chatbot-ui |
| `RAG_JWT_AUDIENCE` | Exact value configured on chatbot-ui |

Actual PEM newlines and literal `\n` escapes are accepted. Missing or malformed
configuration prevents service startup. The private signing key belongs only on
chatbot-ui, separate from Auth.js's `AUTH_SECRET`. Use different keys and claim
values in each environment. Settings are cached until restart/redeployment.
`.env.example` documents variable names; the service reads environment variables
and does not automatically load this file.

Generate keys outside either repository on a trusted machine:

```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out rag-private.pem
openssl pkey -in rag-private.pem -pubout -out rag-public.pem
```

Supply the private PEM through chatbot-ui's server secret configuration and the
public PEM through Cloud Run's environment/secret configuration. Also configure
issuer/audience on Cloud Run before deploying the enforcing revision. Existing
`cloudbuild.yaml` preserves these settings; the processing job does not issue or
verify HTTP user tokens and does not need the signing key or these variables.

## Rollout and API behavior

Configure both services first, deploy the signing chatbot-ui revision, then
immediately deploy this enforcing FastAPI revision. The old backend accepts the
extra header, but is still unprotected until this revision is serving traffic.
Use a maintenance window if that transition is unacceptable. Existing UI users
must complete Google sign-in again. Smoke-test login, upload/status, chat/history
and deletion, plus 401 without a token and 403 for a different requested email.
These changes do not deploy or change Cloud Run IAM. If IAM also requires
authentication, a separate Google service identity mechanism is required; this
application JWT does not satisfy Cloud Run IAM.

Only `/` and `/health` are unauthenticated operational handlers. OpenAPI and
Swagger documentation remain available and advertise bearer authentication.
The legacy `/upload` test page is protected; its plain HTML form cannot attach a
bearer token, so use Swagger's Authorize button or the chatbot-ui upload flow.
Health failures do not include raw database exceptions in responses.

Add future business endpoints to the protected `api` router in `main.py`, and
use `CurrentUser` to enforce ownership. Do not add user operations directly to
the public application. This grants access to verified chatbot-ui Google users,
not an approved membership list. CORS/origin headers are not backend identity.

One public key is accepted at a time. Rotate keys in a coordinated maintenance
window; mixed revisions with different keys will produce temporary 401s.
Tokens may be replayed if stolen and remain valid until expiry after logout
(five minutes plus clock tolerance). Keep transport HTTPS and tokens server-side.

## Authentication tests

```sh
python -m pip install -r requirements-auth-test.txt
python -m pytest tests/test_auth.py
```

The tests exercise real RSA signatures, FastAPI dependencies and HTTP routes.
Database, LLM, and storage services are stubbed so no cloud credentials or
production data are needed. The separate Docling test suite still uses
`requirements-dev.txt`.
