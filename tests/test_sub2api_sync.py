import unittest
from unittest import mock

from services.sub2api_sync import (
    sync_chatgpt_sub2api_status,
    sync_chatgpt_sub2api_status_batch,
)


class _DummyAccount:
    def __init__(self, account_id=1, email="demo@example.com", **extra):
        self.id = account_id
        self.email = email
        self.user_id = extra.get("user_id", "")
        self.token = extra.get("token", "")
        self.extra = dict(extra.get("extra", {}))

    def get_extra(self):
        return dict(self.extra)


class Sub2ApiSyncTests(unittest.TestCase):
    def test_sync_returns_not_found_when_remote_missing(self):
        account = _DummyAccount(
            extra={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "account_id": "acct-1",
                "client_id": "client-1",
            }
        )

        with mock.patch("services.sub2api_sync.list_sub2api_accounts", return_value=[]):
            result = sync_chatgpt_sub2api_status(account, api_url="https://sub2api.example.com", api_key="demo")

        self.assertEqual(result["remote_state"], "not_found")
        self.assertFalse(result["uploaded"])
        self.assertIn("Sub2API", result["message"])

    def test_sync_uses_best_matching_remote_account(self):
        account = _DummyAccount(
            extra={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "account_id": "acct-1",
                "client_id": "client-1",
            }
        )
        remote_items = [
            {
                "id": 22,
                "name": "demo@example.com",
                "status": "active",
                "schedulable": True,
                "updated_at": "2026-04-01T00:00:00Z",
                "credentials": {
                    "chatgpt_account_id": "other",
                    "refresh_token": "other-refresh",
                    "client_id": "client-1",
                },
            },
            {
                "id": 23,
                "name": "demo@example.com",
                "status": "active",
                "schedulable": True,
                "updated_at": "2026-04-02T00:00:00Z",
                "credentials": {
                    "chatgpt_account_id": "acct-1",
                    "refresh_token": "refresh-token",
                    "client_id": "client-1",
                },
            },
        ]

        with mock.patch("services.sub2api_sync.list_sub2api_accounts", return_value=remote_items):
            result = sync_chatgpt_sub2api_status(account, api_url="https://sub2api.example.com", api_key="demo")

        self.assertTrue(result["uploaded"])
        self.assertEqual(result["remote_state"], "usable")
        self.assertEqual(result["sub2api_id"], 23)

    def test_sync_maps_error_status_to_account_deactivated(self):
        account = _DummyAccount(extra={"access_token": "access-token"})
        remote_items = [
            {
                "id": 99,
                "name": "demo@example.com",
                "status": "error",
                "schedulable": False,
                "error_message": "This account has been deleted or deactivated.",
                "credentials": {},
            }
        ]

        with mock.patch("services.sub2api_sync.list_sub2api_accounts", return_value=remote_items):
            result = sync_chatgpt_sub2api_status(account, api_url="https://sub2api.example.com", api_key="demo")

        self.assertEqual(result["remote_state"], "account_deactivated")
        self.assertTrue(result["uploaded"])

    def test_batch_sync_returns_results_for_each_account(self):
        account_a = _DummyAccount(
            account_id=1,
            email="a@example.com",
            extra={"access_token": "token-a", "account_id": "acct-a"},
        )
        account_b = _DummyAccount(
            account_id=2,
            email="b@example.com",
            extra={"access_token": "token-b", "account_id": "acct-b"},
        )

        def fake_list(search, **kwargs):
            if search == "a@example.com":
                return [
                    {
                        "id": 11,
                        "name": "a@example.com",
                        "status": "active",
                        "schedulable": True,
                        "credentials": {"chatgpt_account_id": "acct-a"},
                    }
                ]
            if search == "b@example.com":
                return []
            return []

        with mock.patch("services.sub2api_sync.list_sub2api_accounts", side_effect=fake_list):
            result = sync_chatgpt_sub2api_status_batch(
                [account_a, account_b],
                api_url="https://sub2api.example.com",
                api_key="demo",
            )

        self.assertEqual(result[1]["remote_state"], "usable")
        self.assertEqual(result[2]["remote_state"], "not_found")


if __name__ == "__main__":
    unittest.main()
