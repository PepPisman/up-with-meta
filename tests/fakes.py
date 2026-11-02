import requests


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, invalid_json=False):
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("not json")
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")
