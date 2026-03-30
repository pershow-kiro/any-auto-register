import base64
import json
from types import SimpleNamespace

from platforms.chatgpt import sub2api_upload


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def _b64url_json(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_access_token() -> str:
    header = _b64url_json({"alg": "none", "typ": "JWT"})
    payload = _b64url_json(
        {
            "exp": 1893456000,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-123",
                "organization_id": "ws-123",
            },
        }
    )
    return f"{header}.{payload}.sig"


def test_upload_to_sub2api_uses_sub2api_data_format(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=201)

    monkeypatch.setattr(sub2api_upload.cffi_requests, "post", fake_post)

    account = SimpleNamespace(
        email="tester@example.com",
        access_token=_make_access_token(),
        refresh_token="refresh-token",
        client_id="client-123",
        workspace_id="ws-123",
        account_id="acct-123",
        user_id="user-123",
        extra={},
    )

    success, message = sub2api_upload.upload_to_sub2api(
        account,
        api_url="https://sub2api.example.com/",
        api_key="key-123",
        concurrency=5,
        priority=80,
    )

    assert success is True
    assert message == "成功上传 1 个账号到 Sub2API"
    assert calls[0]["url"] == "https://sub2api.example.com/api/v1/admin/accounts/data"
    payload = calls[0]["kwargs"]["json"]
    assert payload["data"]["type"] == "sub2api-data"
    assert payload["data"]["accounts"][0]["concurrency"] == 5
    assert payload["data"]["accounts"][0]["priority"] == 80
    assert payload["data"]["accounts"][0]["credentials"]["access_token"] == account.access_token


def test_upload_to_sub2api_rejects_missing_access_token():
    account = SimpleNamespace(email="tester@example.com", access_token="", extra={})

    success, message = sub2api_upload.upload_to_sub2api(
        account,
        api_url="https://sub2api.example.com",
        api_key="key-123",
    )

    assert success is False
    assert message == "账号缺少 access_token"


def test_upload_to_sub2api_binds_group_ids_after_import(monkeypatch):
    post_calls = []
    get_calls = []
    remote_item = {
        "id": 88,
        "name": "tester@example.com",
        "credentials": {
            "access_token": _make_access_token(),
            "refresh_token": "refresh-token",
            "chatgpt_account_id": "acct-123",
            "client_id": "client-123",
        },
    }
    state = {"get_count": 0}

    def fake_get(url, **kwargs):
        get_calls.append({"url": url, "kwargs": kwargs})
        state["get_count"] += 1
        if state["get_count"] == 1:
            return FakeResponse(status_code=200, payload={"data": {"items": [], "total": 0}})
        return FakeResponse(status_code=200, payload={"data": {"items": [remote_item], "total": 1}})

    def fake_post(url, **kwargs):
        post_calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=201)

    monkeypatch.setattr(sub2api_upload.cffi_requests, "get", fake_get)
    monkeypatch.setattr(sub2api_upload.cffi_requests, "post", fake_post)

    account = SimpleNamespace(
        email="tester@example.com",
        access_token=remote_item["credentials"]["access_token"],
        refresh_token="refresh-token",
        client_id="client-123",
        workspace_id="ws-123",
        account_id="acct-123",
        user_id="user-123",
        extra={},
    )

    success, message = sub2api_upload.upload_to_sub2api(
        account,
        api_url="https://sub2api.example.com",
        api_key="key-123",
        group_ids="101, 102",
    )

    assert success is True
    assert "并已绑定到分组 [101, 102]" in message
    assert len(get_calls) == 2
    assert post_calls[0]["url"] == "https://sub2api.example.com/api/v1/admin/accounts/data"
    assert post_calls[1]["url"] == "https://sub2api.example.com/api/v1/admin/accounts/bulk-update"
    assert post_calls[1]["kwargs"]["json"] == {"account_ids": [88], "group_ids": [101, 102]}
