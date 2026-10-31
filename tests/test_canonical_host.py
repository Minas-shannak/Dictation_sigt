"""localhost:<port> redirects to 127.0.0.1:<port>, so all browser data stays under one address."""

import pytest

import app as app_module


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.mark.parametrize("path, location", [
    ("/", "http://127.0.0.1:8000/"),
    ("/static/app.js?v=7", "http://127.0.0.1:8000/static/app.js?v=7"),
    ("/api/jobs/abc?x=1&y=two%20words", "http://127.0.0.1:8000/api/jobs/abc?x=1&y=two%20words"),
    ("/media/local:" + "a" * 64, "http://127.0.0.1:8000/media/local:" + "a" * 64),
])
def test_localhost_redirects_keeping_path_and_query(client, path, location):
    r = client.get(path, headers={"Host": "localhost:8000"})
    assert r.status_code == 307 and r.headers["Location"] == location


def test_redirect_keeps_the_method_and_port(client):
    r = client.post("/api/youtube", json={"url": "jNQXAC9IVRw"}, headers={"Host": "LOCALHOST:8123"})
    assert r.status_code == 307 and r.headers["Location"] == "http://127.0.0.1:8123/api/youtube"


def test_canonical_address_is_served_directly(client):
    r = client.get("/", headers={"Host": "127.0.0.1:8000"})
    assert r.status_code == 200 and b"Dictation" in r.data


def test_host_without_port_is_not_redirected(client):
    # Flask's test client uses "localhost" without a port; the other tests rely on it.
    assert client.get("/").status_code == 200
