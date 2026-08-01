import pytest
from fastapi.testclient import TestClient
from media_app_agent import main


class FakeRepository:
    """In-memory stand-in that mirrors the blob layout `Repository` writes.

    Votes are keyed by `(date, kind, voter)` exactly like the blob name, so the
    create-if-absent rule the real repository gets from `If-None-Match: *` is
    reproduced here as a plain dict insert.
    """

    def __init__(self) -> None:
        self.votes: dict[tuple[str, str, str], int] = {}

    def videos(self) -> list[dict]:
        return [{"run_date": "2026-07-25", "title": "每日财经", "status": "ready"}]

    def record_reaction(self, video_date: str, reaction: main.Reaction, voter: str) -> bool:
        main.Repository._require_date(video_date)
        key = (video_date, reaction.kind, voter)
        if key in self.votes:
            return False
        self.votes[key] = reaction.value
        return True

    def reaction_summary(self, video_date: str, voter: str) -> dict:
        main.Repository._require_date(video_date)
        scores = [
            value
            for (date, kind, _), value in self.votes.items()
            if date == video_date and kind == "star"
        ]
        likes = [
            owner
            for (date, kind, owner) in self.votes
            if date == video_date and kind == "like"
        ]
        return {
            "likes": len(likes),
            "stars": {
                "votes": len(scores),
                "total": sum(scores),
                "average": round(sum(scores) / len(scores), 1) if scores else 0.0,
            },
            "mine": {
                "like": voter in likes,
                "star": self.votes.get((video_date, "star", voter), 0),
            },
        }


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(main, "repository", FakeRepository())
    return TestClient(main.app)


def post(client: TestClient, kind: str, value: int, address: str):
    return client.post(
        "/api/videos/260725/reactions",
        json={"kind": kind, "value": value},
        headers={"X-Forwarded-For": address},
    )


def test_catalog(client: TestClient) -> None:
    response = client.get("/api/videos", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.json()[0]["title"] == "每日财经"
    assert response.headers["cache-control"] == "public, max-age=60"


def test_large_catalog_is_compressed(monkeypatch) -> None:
    class LargeRepository(FakeRepository):
        def videos(self) -> list[dict]:
            return [{"title": "每日财经" * 500}]

    monkeypatch.setattr(main, "repository", LargeRepository())
    with TestClient(main.app) as test_client:
        response = test_client.get(
            "/api/videos", headers={"Accept-Encoding": "gzip"}
        )

    assert response.headers["content-encoding"] == "gzip"


def test_repository_catalog_is_cached_and_copied(monkeypatch) -> None:
    store = object.__new__(main.Repository)
    store._catalog_cache = None
    store._catalog_cached_at = 0.0
    store._catalog_lock = main.Lock()
    loads = 0

    def load() -> list[dict]:
        nonlocal loads
        loads += 1
        return [{"run_date": "2026-07-25", "title": "每日财经"}]

    monkeypatch.setattr(store, "_load_videos", load)
    first = store.videos()
    first[0]["title"] = "changed"

    assert store.videos()[0]["title"] == "每日财经"
    assert loads == 1


def test_repository_catalog_refreshes_after_ttl(monkeypatch) -> None:
    store = object.__new__(main.Repository)
    store._catalog_cache = [{"run_date": "2026-07-24"}]
    store._catalog_cached_at = 100.0
    store._catalog_lock = main.Lock()
    monkeypatch.setattr(main, "monotonic", lambda: 100.0 + main.CATALOG_CACHE_SECONDS)
    monkeypatch.setattr(
        store,
        "_load_videos",
        lambda: [{"run_date": "2026-07-25"}],
    )

    assert store.videos()[0]["run_date"] == "2026-07-25"


def test_first_like_is_recorded_and_counted(client: TestClient) -> None:
    response = post(client, "like", 1, "203.0.113.7")
    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] is True
    assert body["likes"] == 1
    assert body["mine"]["like"] is True


def test_second_like_from_same_address_is_rejected(client: TestClient) -> None:
    post(client, "like", 1, "203.0.113.7")
    response = post(client, "like", 1, "203.0.113.7")
    assert response.status_code == 409
    body = response.json()
    assert body["accepted"] is False
    # The rejected call still reports the live total rather than double counting.
    assert body["likes"] == 1


def test_likes_accumulate_across_addresses(client: TestClient) -> None:
    post(client, "like", 1, "203.0.113.7")
    post(client, "like", 1, "198.51.100.4")
    response = client.get(
        "/api/videos/260725/reactions", headers={"X-Forwarded-For": "192.0.2.9"}
    )
    body = response.json()
    assert body["likes"] == 2
    assert body["mine"]["like"] is False


def test_star_is_recorded_with_its_score(client: TestClient) -> None:
    response = post(client, "star", 5, "203.0.113.7")
    assert response.status_code == 201
    body = response.json()
    assert body["stars"] == {"votes": 1, "total": 5, "average": 5.0}
    assert body["mine"]["star"] == 5


def test_second_star_from_same_address_cannot_change_the_score(client: TestClient) -> None:
    post(client, "star", 5, "203.0.113.7")
    response = post(client, "star", 1, "203.0.113.7")
    assert response.status_code == 409
    assert response.json()["stars"]["total"] == 5


def test_average_spans_every_voter(client: TestClient) -> None:
    post(client, "star", 5, "203.0.113.7")
    post(client, "star", 4, "198.51.100.4")
    post(client, "star", 3, "192.0.2.9")
    body = client.get("/api/videos/260725/reactions").json()
    assert body["stars"] == {"votes": 3, "total": 12, "average": 4.0}


def test_like_and_star_are_independent_allowances(client: TestClient) -> None:
    assert post(client, "like", 1, "203.0.113.7").status_code == 201
    assert post(client, "star", 4, "203.0.113.7").status_code == 201


def test_like_value_cannot_be_inflated(client: TestClient) -> None:
    body = post(client, "like", 5, "203.0.113.7").json()
    assert body["likes"] == 1
    assert body["stars"]["votes"] == 0


def test_star_outside_one_to_five_is_rejected(client: TestClient) -> None:
    response = post(client, "star", 9, "203.0.113.7")
    assert response.status_code == 422


def test_malformed_date_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/videos/2026-07-25/reactions", json={"kind": "like", "value": 1}
    )
    assert response.status_code == 400


def test_voter_id_ignores_the_ingress_supplied_port() -> None:
    def voter(headers: dict[str, str]) -> str:
        request = type("Request", (), {"headers": headers, "client": None})()
        return main.voter_id(request)

    assert voter({"x-forwarded-for": "203.0.113.7:41234"}) == voter(
        {"x-forwarded-for": "203.0.113.7"}
    )
    # Only the original caller matters; proxies appended after it must not
    # split one address into several voters.
    assert voter({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}) == voter(
        {"x-forwarded-for": "203.0.113.7, 10.0.0.2"}
    )
    assert voter({"x-forwarded-for": "203.0.113.7"}) != voter(
        {"x-forwarded-for": "198.51.100.4"}
    )
    # The stored id must not be a reversible copy of the address.
    assert "203.0.113.7" not in voter({"x-forwarded-for": "203.0.113.7"})
