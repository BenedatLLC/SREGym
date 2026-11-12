"""The k8srca shim starts a diagnosis and polls for it, so no connection sits idle for ten minutes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from clients.k8srca import driver


class _Service:
    """A stand-in for the host-side service: replies are scripted per request."""

    def __init__(self, start=(202, {"job": "7"}), polls=((200, {"answer": "the cause"}),)):
        self.start, self.polls, self.seen = start, list(polls), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                outer.seen.append(("POST", self.path))
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self._send(*outer.start)

            def do_GET(self):
                outer.seen.append(("GET", self.path))
                self._send(*(outer.polls.pop(0) if len(outer.polls) > 1 else outer.polls[0]))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(driver, "POLL_SECONDS", 0.01)


def ask(svc, monkeypatch, timeout=5):
    monkeypatch.setenv("K8SRCA_SERVICE_URL", svc.url)
    try:
        return driver.ask_service({"app_name": "X"}, "anon_1", timeout)
    finally:
        svc.close()


def test_it_starts_the_job_then_polls_until_the_answer(monkeypatch):
    svc = _Service(polls=[(202, {"status": "running"})] * 3 + [(200, {"answer": "the cause"})])
    assert ask(svc, monkeypatch) == "the cause"
    assert svc.seen[0] == ("POST", "/diagnose/start")
    assert [m for m, _ in svc.seen].count("GET") == 4 and svc.seen[-1] == ("GET", "/diagnose/result/7")


def test_a_refused_start_is_an_error(monkeypatch):
    svc = _Service(start=(409, {"error": "a diagnosis is already running"}))
    with pytest.raises(RuntimeError, match="409"):
        ask(svc, monkeypatch)


def test_a_failed_diagnosis_surfaces_the_services_error(monkeypatch):
    svc = _Service(polls=[(502, {"error": "no usable answer: incomplete turn"})])
    with pytest.raises(RuntimeError, match="502.*incomplete turn"):
        ask(svc, monkeypatch)


def test_an_empty_answer_is_an_error(monkeypatch):
    svc = _Service(polls=[(200, {"answer": "  "})])
    with pytest.raises(RuntimeError, match="no answer"):
        ask(svc, monkeypatch)


def test_it_stops_waiting_at_the_deadline(monkeypatch):
    svc = _Service(polls=[(202, {"status": "running"})])
    with pytest.raises(TimeoutError):
        ask(svc, monkeypatch, timeout=0.2)


def test_a_few_failed_polls_are_survived_and_many_are_not(monkeypatch):
    calls = {"n": 0}
    real_get = requests.get

    def flaky(url, **kw):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise requests.ConnectionError("proxy hiccup")
        return real_get(url, **kw)

    monkeypatch.setattr(driver.requests, "get", flaky)
    assert ask(_Service(), monkeypatch) == "the cause"

    def dead(url, **kw):
        raise requests.ConnectionError("gone")

    monkeypatch.setattr(driver.requests, "get", dead)
    with pytest.raises(RuntimeError, match="stopped answering"):
        ask(_Service(), monkeypatch)
