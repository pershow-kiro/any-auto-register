import unittest

from core.base_mailbox import FreemailMailbox, MailboxAccount


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResponse(self.payload)


class FreemailMailboxTests(unittest.TestCase):
    def test_wait_for_code_skips_excluded_codes(self):
        mailbox = FreemailMailbox.__new__(FreemailMailbox)
        mailbox.api = "https://mail.example.com"
        mailbox._session = FakeSession(
            [
                {
                    "id": 1,
                    "verification_code": "111111",
                    "preview": "Old code 111111",
                    "subject": "OpenAI verification",
                },
                {
                    "id": 2,
                    "verification_code": "222222",
                    "preview": "New code 222222",
                    "subject": "OpenAI verification",
                },
            ]
        )

        code = mailbox.wait_for_code(
            MailboxAccount(email="demo@example.com", account_id="demo@example.com"),
            timeout=1,
            exclude_codes={"111111"},
        )

        self.assertEqual(code, "222222")


if __name__ == "__main__":
    unittest.main()
