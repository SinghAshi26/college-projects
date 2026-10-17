"""Log each HTTP send, including redirect hops and the auditor's bounded retries."""
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from requests.adapters import HTTPAdapter

from storage import StorageError

_store = ContextVar("request_store", default=None)


@contextmanager
def request_tracking(store):
    token = _store.set(store)
    try:
        yield
    finally:
        _store.reset(token)


class TrackingAdapter(HTTPAdapter):
    def __init__(self, store, provider, operation, attempt):
        super().__init__(max_retries=0)
        self.store, self.provider, self.operation, self.attempt = store, provider, operation, attempt

    def send(self, request, **kwargs):
        request_id = self.store.reserve_request(self.provider, self.operation, attempt=self.attempt)
        try:
            response = super().send(request, **kwargs)
            if not kwargs.get("stream"):
                # Account for body-read failures before declaring a completed success.
                response.content
        except requests.Timeout:
            self.store.complete_request(request_id, "timeout")
            raise
        except requests.RequestException:
            self.store.complete_request(request_id, "network_error")
            raise
        self.store.complete_request(request_id, "success" if response.status_code < 400 else "http_error", response.status_code)
        return response


def tracked_http(method, url, *, provider="website", operation=None, attempt=1, **kwargs):
    store = _store.get()
    if store is None:
        # CLI callers must establish the same persistent accounting as the UI.
        from configuration import configured_store
        store = configured_store()
    with requests.Session() as session:
        adapter = TrackingAdapter(store, provider, operation or f"website_{method.lower()}", attempt)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.max_redirects = 5
        return session.request(method, url, **kwargs)
