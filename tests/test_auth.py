"""Real JWT/HTTP validation with external database, storage and LLM calls stubbed."""
import importlib
import sys
import time
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from auth import auth_settings


@pytest.fixture
def keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("RAG_JWT_PUBLIC_KEY", public)
    monkeypatch.setenv("RAG_JWT_ISSUER", "chatbot-ui-test")
    monkeypatch.setenv("RAG_JWT_AUDIENCE", "tree-rag-test")
    auth_settings.cache_clear()
    yield key
    auth_settings.cache_clear()


@pytest.fixture
def token(keys):
    def make(overrides=None, omit=None, algorithm="RS256", key=None):
        now = int(time.time())
        claims = dict(sub="Alice@Example.com", name="Alice", iss="chatbot-ui-test",
                      aud="tree-rag-test", iat=now, exp=now + 300)
        claims.update(overrides or {})
        if omit:
            claims.pop(omit)
        return jwt.encode(claims, keys if key is None else key, algorithm=algorithm)
    return make


@pytest.fixture
def api(monkeypatch, keys):
    services = {}
    definitions = {
        "db": {"engine": Mock(), "close_connector": Mock()},
        "db_init": {"ensure_database_schema": Mock()},
        "chatbot_service": {name: Mock() for name in (
            "answer_query", "delete_conversation", "get_conversation_messages", "list_conversations")},
        "upload_service": {"upload_files_and_record_metadata": AsyncMock(), "get_folder_processing_status": Mock()},
        "user_service": {"record_user_login": Mock()},
    }
    for module_name, attributes in definitions.items():
        module = ModuleType(module_name)
        for name, value in attributes.items():
            setattr(module, name, value)
            services[name] = value
        monkeypatch.setitem(sys.modules, module_name, module)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    with TestClient(main.app) as client:
        yield client, services
    sys.modules.pop("main", None)


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def requests_for(user):
    return [
        ("POST", "/users/login", {"json": {"id": user, "name": "Untrusted name"}}),
        ("POST", "/chat/query", {"json": {"user_id": user, "query": "hello"}}),
        ("GET", "/chat/conversations", {"params": {"user_id": user}}),
        ("GET", "/chat/conversations/123", {"params": {"user_id": user}}),
        ("DELETE", "/chat/conversations/123", {"params": {"user_id": user}}),
        ("GET", "/files/processing-status", {"params": {"id": user, "folder_name": "docs"}}),
        ("POST", "/files/upload", {"data": {"id": user, "folder_name": "docs"},
                                  "files": {"files": ("test.txt", b"test", "text/plain")}}),
    ]


def test_all_business_routes_require_bearer(api):
    client, services = api
    for method, path, kwargs in requests_for("Alice@Example.com"):
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"
    assert client.get("/upload").status_code == 401
    services["answer_query"].assert_not_called()


@pytest.mark.parametrize("changes,omit", [
    ({"iss": "other"}, None), ({"aud": "other"}, None),
    ({"aud": ["tree-rag-test", "other"]}, None),
    ({"exp": 1}, None), ({"iat": int(time.time()) + 600}, None),
    ({"nbf": int(time.time()) + 600}, None),
    ({"exp": int(time.time()) + 3600}, None),
    ({"sub": ""}, None), ({"sub": " Alice@Example.com"}, None),
    ({"sub": 123}, None), ({"iat": "123"}, None),
    ({"name": {}}, None), ({"exp": {}}, None), ({"iat": {}}, None),
    ({}, "exp"), ({}, "iat"), ({}, "iss"), ({}, "aud"), ({}, "sub"),
])
def test_invalid_claims_rejected(api, token, changes, omit):
    client, _ = api
    response = client.get("/chat/conversations", params={"user_id": "Alice@Example.com"},
                          headers=headers(token(changes, omit)))
    assert response.status_code == 401


def test_bad_signature_algorithm_and_malformed_tokens(api, token):
    client, _ = api
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for value in ("not-a-jwt", token(key=other_key),
                  token(algorithm="HS256", key="x" * 32), token(algorithm="none", key="")):
        response = client.get("/chat/conversations", params={"user_id": "Alice@Example.com"},
                              headers=headers(value))
        assert response.status_code == 401


def test_cross_user_ids_rejected_before_services(api, token):
    client, services = api
    for method, path, kwargs in requests_for("bob@example.com"):
        response = client.request(method, path, headers=headers(token()), **kwargs)
        assert response.status_code == 403, path
    for name in ("answer_query", "list_conversations", "get_conversation_messages",
                 "delete_conversation", "record_user_login", "get_folder_processing_status",
                 "upload_files_and_record_metadata"):
        services[name].assert_not_called()


def test_valid_identity_is_preserved_and_name_comes_from_token(api, token):
    client, services = api
    services["list_conversations"].return_value = {"conversations": []}
    response = client.get("/chat/conversations", params={"user_id": "Alice@Example.com"},
                          headers=headers(token()))
    assert response.status_code == 200
    services["list_conversations"].assert_called_once_with(user_id="Alice@Example.com")
    services["record_user_login"].return_value = {
        "id": "Alice@Example.com", "name": "Alice", "last_login_date": "2026-01-01T00:00:00Z"}
    response = client.post("/users/login", headers=headers(token()),
                           json={"id": "Alice@Example.com", "name": "Forged name"})
    assert response.status_code == 200
    recorded = services["record_user_login"].call_args.args[0]
    assert recorded.id == "Alice@Example.com"
    assert recorded.name == "Alice"


@pytest.mark.parametrize("name", ["RAG_JWT_PUBLIC_KEY", "RAG_JWT_ISSUER", "RAG_JWT_AUDIENCE"])
def test_missing_settings_fail_closed(keys, monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name):
        auth_settings()


def test_invalid_key_fails_closed(keys, monkeypatch):
    monkeypatch.setenv("RAG_JWT_PUBLIC_KEY", "invalid key")
    with pytest.raises(ValueError):
        auth_settings()


def test_public_root_and_health_failure_do_not_leak_database_errors(api, monkeypatch):
    client, _ = api
    assert client.get("/").status_code == 200
    monkeypatch.setattr(sys.modules["main"], "Session", Mock(side_effect=RuntimeError("secret DB URL")))
    response = client.get("/health")
    assert response.status_code == 503
    assert "secret" not in response.text
