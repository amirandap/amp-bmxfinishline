"""FastAPI endpoint integration tests.

These tests run against an in-memory SQLite DB and do not require ML dependencies
(YOLO / PaddleOCR) to be installed – the pipeline is never actually started.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, get_db
from app.main import app


# ── Test DB setup ──────────────────────────────────────────────────

SQLITE_URL = "sqlite:///./test_api.db"


def _make_engine():
    return create_engine(SQLITE_URL, connect_args={"check_same_thread": False})


@pytest.fixture(scope="module")
def client():
    engine = _make_engine()
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    import os
    if os.path.exists("test_api.db"):
        os.remove("test_api.db")


# ── Health ─────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ── Race CRUD ──────────────────────────────────────────────────────

class TestRaces:
    def test_list_empty(self, client):
        r = client.get("/races")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_race(self, client):
        r = client.post("/races", json={"name": "Test Race"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Test Race"
        assert "id" in body
        TestRaces._race_id = body["id"]

    def test_list_returns_created(self, client):
        r = client.get("/races")
        assert r.status_code == 200
        names = [race["name"] for race in r.json()]
        assert "Test Race" in names

    def test_get_race(self, client):
        r = client.get(f"/races/{TestRaces._race_id}")
        assert r.status_code == 200
        assert r.json()["id"] == TestRaces._race_id

    def test_get_race_not_found(self, client):
        r = client.get("/races/doesnotexist")
        assert r.status_code == 404

    def test_create_race_blank_name(self, client):
        r = client.post("/races", json={"name": ""})
        assert r.status_code == 422

    def test_delete_race(self, client):
        # Create a throwaway race to delete
        r = client.post("/races", json={"name": "DeleteMe"})
        rid = r.json()["id"]
        r = client.delete(f"/races/{rid}")
        assert r.status_code == 204
        # Confirm gone
        r = client.get(f"/races/{rid}")
        assert r.status_code == 404

    def test_delete_race_not_found(self, client):
        r = client.delete("/races/doesnotexist")
        assert r.status_code == 404


# ── Calibration ────────────────────────────────────────────────────

class TestCalibration:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        r = client.post("/races", json={"name": "Calib Race"})
        self.race_id = r.json()["id"]

    VALID_CALIB = {
        "roi_polygon": [[0, 0], [1920, 0], [1920, 1080], [0, 1080]],
        "finish_line": [[960, 0], [960, 1080]],
        "reading_zone": [[400, 200], [700, 200], [700, 600], [400, 600]],
    }

    def test_get_calibration_empty(self, client):
        r = client.get(f"/races/{self.race_id}/calibration")
        assert r.status_code == 200
        body = r.json()
        assert body["roi_polygon"] is None
        assert body["finish_line"] is None

    def test_set_calibration(self, client):
        r = client.post(f"/races/{self.race_id}/calibration", json=self.VALID_CALIB)
        assert r.status_code == 200
        body = r.json()
        assert body["finish_line"] == [[960, 0], [960, 1080]]
        assert len(body["roi_polygon"]) == 4

    def test_calibration_invalid_finish_line(self, client):
        bad = dict(self.VALID_CALIB)
        bad["finish_line"] = [[0, 0]]  # only 1 point
        r = client.post(f"/races/{self.race_id}/calibration", json=bad)
        assert r.status_code in (400, 422)

    def test_calibration_invalid_roi(self, client):
        bad = dict(self.VALID_CALIB)
        bad["roi_polygon"] = [[0, 0], [1, 1]]  # only 2 points
        r = client.post(f"/races/{self.race_id}/calibration", json=bad)
        assert r.status_code in (400, 422)

    def test_calibration_race_not_found(self, client):
        r = client.post("/races/notexist/calibration", json=self.VALID_CALIB)
        assert r.status_code == 404


# ── Processing ─────────────────────────────────────────────────────

class TestProcessing:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        r = client.post("/races", json={"name": "Process Race"})
        self.race_id = r.json()["id"]

    def test_status_idle_no_pipeline(self, client):
        r = client.get(f"/races/{self.race_id}/process/status")
        assert r.status_code == 200
        assert r.json()["state"] == "idle"

    def test_start_without_calibration(self, client):
        r = client.post(f"/races/{self.race_id}/process/start", json={
            "input_type": "file", "input": "/tmp/video.mp4"
        })
        assert r.status_code == 400

    def test_stop_no_pipeline(self, client):
        r = client.post(f"/races/{self.race_id}/process/stop")
        assert r.status_code == 404


# ── Arrivals ───────────────────────────────────────────────────────

class TestArrivals:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        r = client.post("/races", json={"name": "Arrival Race"})
        self.race_id = r.json()["id"]
        # Manually insert a fake arrival via DB by calling the pipeline model
        from app.models import get_db as real_get_db
        from app.models.tables import Arrival
        from app.models import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
        Session = sessionmaker(bind=engine)
        with Session() as db:
            a = Arrival(
                race_id=self.race_id,
                track_id=1,
                timestamp_ms=12345.0,
                bib="42",
                confidence=0.90,
                status="AUTO_OK",
                auto_position=1,
            )
            db.add(a)
            db.commit()
            db.refresh(a)
            self.arrival_id = a.id

    def test_list_arrivals(self, client):
        r = client.get(f"/races/{self.race_id}/arrivals")
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 1
        assert items[0]["bib"] == "42"

    def test_list_arrivals_race_not_found(self, client):
        r = client.get("/races/notexist/arrivals")
        assert r.status_code == 404

    def test_patch_arrival_bib(self, client):
        r = client.patch(
            f"/races/{self.race_id}/arrivals/{self.arrival_id}",
            json={"bib": "99", "status": "MANUAL_OK"},
        )
        assert r.status_code == 200
        assert r.json()["bib"] == "99"
        assert r.json()["status"] == "MANUAL_OK"

    def test_patch_invalid_status(self, client):
        r = client.patch(
            f"/races/{self.race_id}/arrivals/{self.arrival_id}",
            json={"status": "INVALID_STATUS"},
        )
        assert r.status_code == 422

    def test_reorder_arrivals(self, client):
        r = client.post(
            f"/races/{self.race_id}/arrivals/reorder",
            json={"arrival_ids_in_order": [self.arrival_id]},
        )
        assert r.status_code == 200
        assert r.json()[0]["manual_position"] == 1

    def test_image_not_found(self, client):
        r = client.get(f"/races/{self.race_id}/arrivals/{self.arrival_id}/image")
        assert r.status_code == 404  # no crossing frame set

    def test_delete_arrival(self, client):
        # Create a second arrival to delete
        from app.models.tables import Arrival
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
        Session = sessionmaker(bind=engine)
        with Session() as db:
            a = Arrival(
                race_id=self.race_id,
                track_id=2,
                timestamp_ms=99999.0,
                bib="77",
                confidence=0.5,
                status="NEEDS_REVIEW",
                auto_position=2,
            )
            db.add(a)
            db.commit()
            db.refresh(a)
            aid = a.id

        r = client.delete(f"/races/{self.race_id}/arrivals/{aid}")
        assert r.status_code == 204
        # Confirm not in list
        items = client.get(f"/races/{self.race_id}/arrivals").json()
        assert all(x["id"] != aid for x in items)

    def test_delete_arrival_not_found(self, client):
        r = client.delete(f"/races/{self.race_id}/arrivals/doesnotexist")
        assert r.status_code == 404


# ── Export ─────────────────────────────────────────────────────────

class TestExport:
    @pytest.fixture(autouse=True)
    def setup(self, client):
        r = client.post("/races", json={"name": "Export Race"})
        self.race_id = r.json()["id"]

    def test_export_csv_empty(self, client):
        r = client.get(f"/races/{self.race_id}/export.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        lines = r.text.strip().splitlines()
        assert lines[0].startswith("position")

    def test_export_json_empty(self, client):
        r = client.get(f"/races/{self.race_id}/export.json")
        assert r.status_code == 200
        body = r.json()
        assert body["race_id"] == self.race_id
        assert body["results"] == []

    def test_export_race_not_found(self, client):
        r = client.get("/races/notexist/export.csv")
        assert r.status_code == 404
