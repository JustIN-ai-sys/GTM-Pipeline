import json
import threading
import urllib.error
import urllib.request

import pytest

from gtm_pipeline.db import connect, init_db
from gtm_pipeline.webhook import make_server

PAYLOAD = {
    "datas": [
        {
            "custom": {"person_id": "1"},
            "contact": {
                "most_probable_email": "ada@acme.example",
                "most_probable_email_status": "DELIVERABLE",
            },
        }
    ]
}


@pytest.fixture
def server(tmp_path):
    db = tmp_path / "hook.db"
    conn = connect(db)
    init_db(conn)
    conn.execute("INSERT INTO organizations (id, name) VALUES (1, 'Acme')")
    conn.execute("INSERT INTO people (id, org_id, first_name) VALUES (1, 1, 'Ada')")
    conn.commit()
    conn.close()
    httpd = make_server(str(db), token="s3cret", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, db
    httpd.shutdown()
    httpd.server_close()


def _post(httpd, path, body):
    url = f"http://127.0.0.1:{httpd.server_address[1]}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=5)


def test_valid_webhook_stores_email(server):
    httpd, db = server
    with _post(httpd, "/webhook/fullenrich?token=s3cret", PAYLOAD) as response:
        assert json.loads(response.read()) == {"updated": 1, "not_found": 0}
    row = connect(db).execute("SELECT email, email_status, email_source FROM people").fetchone()
    assert (row["email"], row["email_status"]) == ("ada@acme.example", "deliverable")
    assert row["email_source"] == "fullenrich_webhook"


def test_wrong_token_is_rejected_and_nothing_is_written(server):
    httpd, db = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(httpd, "/webhook/fullenrich?token=nope", PAYLOAD)
    assert exc.value.code == 401
    assert connect(db).execute("SELECT email FROM people").fetchone()["email"] is None


def test_unknown_path_is_404(server):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(httpd, "/other?token=s3cret", PAYLOAD)
    assert exc.value.code == 404
