from neatsprint_task.api.router import TRANSITIONS


def test_transition_graph_is_sane() -> None:
    assert "IN_PROGRESS" in TRANSITIONS["TODO"]
    assert "DONE" in TRANSITIONS["IN_PROGRESS"]
    assert "DONE" in TRANSITIONS["IN_REVIEW"]
    assert "TODO" in TRANSITIONS["BLOCKED"]
    assert "TODO" not in TRANSITIONS["DONE"]


def test_healthz() -> None:
    from fastapi.testclient import TestClient

    from neatsprint_task.main import app

    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "task"
