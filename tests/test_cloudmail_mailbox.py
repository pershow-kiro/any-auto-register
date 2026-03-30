import base64
import json

from core.cloudmail_mailbox import CloudMailMailbox


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def test_cloudmail_create_email(monkeypatch):
    calls = []
    responses = [
        FakeResponse(payload={"code": 200, "data": {"token": "public-token"}}),
        FakeResponse(payload={"code": 200, "data": None}),
    ]

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr("core.cloudmail_mailbox.requests.request", fake_request)

    mailbox = CloudMailMailbox(
        api_url="https://mail.example.com",
        admin_email="admin@example.com",
        admin_password="secret",
        domain="mail.example.com",
    )
    account = mailbox.get_email()

    assert account.email.endswith("@mail.example.com")
    assert account.account_id == account.email
    assert account.extra["password"]
    assert calls[0]["url"] == "https://mail.example.com/api/public/genToken"
    assert calls[1]["url"] == "https://mail.example.com/api/public/addUser"
    assert calls[1]["kwargs"]["headers"]["Authorization"] == "public-token"


def test_cloudmail_wait_for_code_honors_otp_sent_at(monkeypatch):
    responses = [
        FakeResponse(payload={"code": 200, "data": {"token": "public-token"}}),
        FakeResponse(
            payload={
                "code": 200,
                "data": [
                    {
                        "emailId": 1,
                        "subject": "Old code",
                        "text": "111111",
                        "createTime": "2026-03-23 10:00:00",
                    },
                    {
                        "emailId": 2,
                        "subject": "OpenAI verification code",
                        "text": "Your code is 222222",
                        "createTime": "2026-03-23 10:00:05",
                    },
                ],
            }
        ),
    ]

    def fake_request(method, url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("core.cloudmail_mailbox.requests.request", fake_request)

    mailbox = CloudMailMailbox(
        api_url="https://mail.example.com",
        admin_email="admin@example.com",
        admin_password="secret",
        domain="mail.example.com",
    )
    account = type("Account", (), {"email": "tester@mail.example.com"})()

    code = mailbox.wait_for_code(
        account,
        timeout=1,
        otp_sent_at=1742724002,
    )

    assert code == "222222"
