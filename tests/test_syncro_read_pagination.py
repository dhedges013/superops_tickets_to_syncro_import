import syncro_read


def test_syncro_api_get_uses_total_pages(monkeypatch):
    responses = [
        {"customers": [{"id": 1}], "meta": {"total_pages": 2}},
        {"customers": [{"id": 2}], "meta": {"total_pages": 2}},
    ]
    calls = []

    def fake_api_call(method, endpoint, params=None):
        calls.append(dict(params))
        return responses.pop(0)

    monkeypatch.setattr(syncro_read, "syncro_api_call", fake_api_call)
    monkeypatch.setattr(syncro_read.time, "sleep", lambda _: None)

    assert syncro_read.syncro_api_get("/customers") == [{"id": 1}, {"id": 2}]
    assert [call["page"] for call in calls] == [1, 2]


def test_syncro_api_get_supports_next_page(monkeypatch):
    responses = [
        {"customers": [{"id": 1}], "meta": {"next_page": 2}},
        {"customers": [{"id": 2}], "meta": {"next_page": None}},
    ]

    monkeypatch.setattr(
        syncro_read,
        "syncro_api_call",
        lambda method, endpoint, params=None: responses.pop(0),
    )
    monkeypatch.setattr(syncro_read.time, "sleep", lambda _: None)

    assert syncro_read.syncro_api_get("/customers") == [{"id": 1}, {"id": 2}]
