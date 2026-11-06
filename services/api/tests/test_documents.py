from tests.conftest import ALICE, BOB

PDF = "application/pdf"


def make_profile(client, headers=ALICE):
    return client.post("/profiles", json={"name": "Me"}, headers=headers).json()


def upload(client, storage, profile_id, headers=ALICE, filename="report.pdf", content_type=PDF, size=2048):
    resp = client.post(
        "/documents/upload-url",
        json={"profile_id": profile_id, "filename": filename, "content_type": content_type},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    key = resp.json()["storage_key"]
    storage.put(key, size=size, content_type=content_type)
    return key


def register(client, profile_id, key, headers=ALICE, name="report.pdf"):
    return client.post(
        "/documents",
        json={"profile_id": profile_id, "storage_key": key, "original_name": name},
        headers=headers,
    )


def test_upload_register_view_delete(client, storage, pipeline):
    profile = make_profile(client)
    key = upload(client, storage, profile["id"], filename="../../Blood Test (Jan).pdf")
    assert key.endswith("/Blood_Test_Jan_.pdf")

    resp = register(client, profile["id"], key)
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["status"] == "queued"
    assert doc["size_bytes"] == 2048
    assert doc["mime"] == PDF

    assert [str(i) for i in pipeline.started] == [doc["id"]]

    listed = client.get("/documents", params={"profile_id": profile["id"]}, headers=ALICE).json()
    assert [d["id"] for d in listed] == [doc["id"]]

    url = client.get(f"/documents/{doc['id']}/file", headers=ALICE).json()["url"]
    assert key in url

    assert register(client, profile["id"], key).status_code == 409

    assert client.delete(f"/documents/{doc['id']}", headers=ALICE).status_code == 204
    assert key not in storage.objects
    assert client.get(f"/documents/{doc['id']}", headers=ALICE).status_code == 404


def test_rejects_bad_uploads(client, storage):
    profile = make_profile(client)
    pid = profile["id"]

    resp = client.post(
        "/documents/upload-url",
        json={"profile_id": pid, "filename": "x.exe", "content_type": "application/x-msdownload"},
        headers=ALICE,
    )
    assert resp.status_code == 415

    # Registered before the browser finished uploading.
    pending = client.post(
        "/documents/upload-url",
        json={"profile_id": pid, "filename": "a.pdf", "content_type": PDF},
        headers=ALICE,
    ).json()["storage_key"]
    resp = register(client, pid, pending)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Upload not found"

    big = upload(client, storage, pid, size=100 * 1024 * 1024)
    assert register(client, pid, big).status_code == 413
    assert big not in storage.objects

    # The object's stored content type must be allowed too, not just the requested one.
    sneaky = upload(client, storage, pid)
    storage.put(sneaky, content_type="text/html")
    assert register(client, pid, sneaky).status_code == 415


def test_isolation(client, storage):
    alice_profile = make_profile(client)
    bob_profile = make_profile(client, headers=BOB)
    key = upload(client, storage, alice_profile["id"])
    doc = register(client, alice_profile["id"], key).json()

    # Bob can't request upload URLs for Alice's profile...
    resp = client.post(
        "/documents/upload-url",
        json={"profile_id": alice_profile["id"], "filename": "a.pdf", "content_type": PDF},
        headers=BOB,
    )
    assert resp.status_code == 404
    # ...or claim Alice's uploaded object under his own profile.
    assert register(client, bob_profile["id"], key, headers=BOB).status_code == 400
    # ...or see, open, or delete her document.
    assert client.get("/documents", headers=BOB).json() == []
    assert client.get(f"/documents/{doc['id']}", headers=BOB).status_code == 404
    assert client.get(f"/documents/{doc['id']}/file", headers=BOB).status_code == 404
    assert client.delete(f"/documents/{doc['id']}", headers=BOB).status_code == 404


def test_deleting_profile_removes_documents_and_files(client, storage):
    profile = make_profile(client)
    other = make_profile(client)
    key = upload(client, storage, profile["id"])
    other_key = upload(client, storage, other["id"])
    doc = register(client, profile["id"], key).json()
    register(client, other["id"], other_key)

    assert client.delete(f"/profiles/{profile['id']}", headers=ALICE).status_code == 204
    assert client.get(f"/documents/{doc['id']}", headers=ALICE).status_code == 404
    assert key not in storage.objects
    assert other_key in storage.objects
    assert len(client.get("/documents", headers=ALICE).json()) == 1


def test_duplicate_upload_rejected(client, storage):
    profile = make_profile(client)
    first = upload(client, storage, profile["id"])
    storage.objects[first] = storage.objects[first].__class__(size=10, content_type=PDF, etag="same")
    assert register(client, profile["id"], first).status_code == 201

    second = upload(client, storage, profile["id"])
    storage.put(second, etag="same")
    resp = register(client, profile["id"], second)
    assert resp.status_code == 409
    assert "already uploaded" in resp.json()["detail"]
    assert second not in storage.objects

    # The same file for a different family member is fine.
    other = make_profile(client)
    third = upload(client, storage, other["id"])
    storage.put(third, etag="same")
    assert register(client, other["id"], third).status_code == 201


def test_queue_unavailable_marks_failed(client, storage, pipeline):
    profile = make_profile(client)
    key = upload(client, storage, profile["id"])
    pipeline.fail = True
    doc = register(client, profile["id"], key).json()
    assert doc["status"] == "failed"
    assert "Reprocess" in doc["error"]

    pipeline.fail = False
    resp = client.post(f"/documents/{doc['id']}/reprocess", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["error"] is None
    # Already queued: a second reprocess is refused.
    assert client.post(f"/documents/{doc['id']}/reprocess", headers=ALICE).status_code == 409


def test_summary_and_pages_endpoints(client, storage):
    from app.db.models import DocumentPage, Summary
    from tests.conftest import TestingSession

    profile = make_profile(client)
    doc = register(client, profile["id"], upload(client, storage, profile["id"])).json()
    assert client.get(f"/documents/{doc['id']}/summary", headers=ALICE).status_code == 404
    assert client.get(f"/documents/{doc['id']}/pages", headers=ALICE).json() == []

    with TestingSession() as db:
        db.add_all([
            DocumentPage(document_id=doc["id"], page_no=2, text="second", engine="docling"),
            DocumentPage(document_id=doc["id"], page_no=1, text="first", engine="tesseract", ocr_confidence=0.9),
            Summary(document_id=doc["id"], content_md="### Overview", model="test-model"),
        ])
        db.commit()

    pages = client.get(f"/documents/{doc['id']}/pages", headers=ALICE).json()
    assert [(p["page_no"], p["text"]) for p in pages] == [(1, "first"), (2, "second")]
    assert client.get(f"/documents/{doc['id']}/summary", headers=ALICE).json()["content_md"] == "### Overview"

    for path in ("summary", "pages"):
        assert client.get(f"/documents/{doc['id']}/{path}", headers=BOB).status_code == 404
    assert client.post(f"/documents/{doc['id']}/reprocess", headers=BOB).status_code == 404
