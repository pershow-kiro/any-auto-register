import unittest
from types import SimpleNamespace

from platforms.chatgpt.register import RegistrationEngine


class FakeCookie:
    def __init__(self, name, value, domain=""):
        self.name = name
        self.value = value
        self.domain = domain


class FakeCookies:
    def __init__(self):
        self._items = []

    def set(self, name, value, domain=None):
        domain = domain or ""
        self._items = [
            item
            for item in self._items
            if not (item.name == name and item.domain == domain)
        ]
        self._items.append(FakeCookie(name=name, value=value, domain=domain))

    def get(self, name, domain=None):
        for item in reversed(self._items):
            if item.name != name:
                continue
            if domain is not None and item.domain != domain:
                continue
            return item.value
        return None

    @property
    def jar(self):
        return list(self._items)


class FakeSession:
    def __init__(self):
        self.cookies = FakeCookies()
        self.visited = []

    def get(self, url, timeout=None):
        self.visited.append((url, timeout))
        return SimpleNamespace(status_code=200, url=url)


class RegistrationEngineDeviceIdTests(unittest.TestCase):
    def test_get_device_id_reuses_same_value_across_calls(self):
        session = FakeSession()
        engine = RegistrationEngine.__new__(RegistrationEngine)
        engine.oauth_start = SimpleNamespace(
            auth_url="https://auth.openai.com/oauth/authorize?prompt=login"
        )
        engine.http_client = SimpleNamespace(session=session)
        engine.session = session
        engine._device_id = None
        engine._log = lambda *args, **kwargs: None

        first = engine._get_device_id()
        second = engine._get_device_id()

        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertEqual(session.cookies.get("oai-did", domain="auth.openai.com"), first)
        self.assertEqual(session.cookies.get("oai-did", domain=".auth.openai.com"), first)
        self.assertEqual(len(session.visited), 2)


if __name__ == "__main__":
    unittest.main()
