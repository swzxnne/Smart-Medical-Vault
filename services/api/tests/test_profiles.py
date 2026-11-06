from tests.conftest import ALICE, BOB


def create(client, headers=ALICE, **fields):
    resp = client.post("/profiles", json={"name": "Mom", **fields}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_crud(client):
    profile = create(client, dob="1960-05-01", sex="female", blood_group="B+", relation="mother")
    assert profile["blood_group"] == "B+"

    assert [p["id"] for p in client.get("/profiles", headers=ALICE).json()] == [profile["id"]]

    resp = client.patch(f"/profiles/{profile['id']}", json={"relation": "mom"}, headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["relation"] == "mom"
    assert resp.json()["name"] == "Mom"

    assert client.delete(f"/profiles/{profile['id']}", headers=ALICE).status_code == 204
    assert client.get(f"/profiles/{profile['id']}", headers=ALICE).status_code == 404


def test_validation(client):
    assert client.post("/profiles", json={"name": ""}, headers=ALICE).status_code == 422
    assert client.post("/profiles", json={"name": "X", "blood_group": "Z"}, headers=ALICE).status_code == 422
    profile = create(client)
    resp = client.patch(f"/profiles/{profile['id']}", json={"name": None}, headers=ALICE)
    assert resp.status_code == 422


def test_isolation(client):
    profile = create(client)
    assert client.get("/profiles", headers=BOB).json() == []
    assert client.get(f"/profiles/{profile['id']}", headers=BOB).status_code == 404
    assert client.patch(f"/profiles/{profile['id']}", json={"name": "x"}, headers=BOB).status_code == 404
    assert client.delete(f"/profiles/{profile['id']}", headers=BOB).status_code == 404
    assert client.get(f"/profiles/{profile['id']}", headers=ALICE).status_code == 200


def test_requires_auth(client):
    assert client.get("/profiles").status_code == 422  # fake auth header missing
