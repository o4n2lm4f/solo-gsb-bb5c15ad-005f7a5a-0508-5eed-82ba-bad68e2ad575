"""端到端接口测试：违规检测、稳定排序、试排级联与计划不可变。"""
import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

db.init_db()  # TestClient 不进入 with 块时不会触发 lifespan，这里显式建表
client = TestClient(app)

MATRIX = {
    "HEAVY": {"HEAVY": 96, "MEDIUM": 120, "LIGHT": 180},
    "MEDIUM": {"HEAVY": 60, "MEDIUM": 60, "LIGHT": 120},
    "LIGHT": {"HEAVY": 60, "MEDIUM": 60, "LIGHT": 60},
}


def make_plan():
    return {
        "name": "测试计划",
        "separation_matrix": MATRIX,
        "runways": {
            "18L": [
                {"flight_no": "CA101", "sched_time": "2026-09-18T08:00:00Z", "wake_category": "HEAVY"},
                {"flight_no": "MU202", "sched_time": "2026-09-18T08:01:00Z", "wake_category": "MEDIUM"},
                {"flight_no": "CZ303", "sched_time": "2026-09-18T08:02:00Z", "wake_category": "LIGHT"},
                {"flight_no": "ZH404", "sched_time": "2026-09-18T08:02:00Z", "wake_category": "HEAVY"},
            ],
            "18R": [
                {"flight_no": "FM505", "sched_time": "2026-09-18T08:00:30Z", "wake_category": "MEDIUM"},
                {"flight_no": "HO606", "sched_time": "2026-09-18T08:02:30Z", "wake_category": "HEAVY"},
            ],
        },
    }


def test_create_plan_and_violations():
    r = client.post("/api/v1/plans", json=make_plan())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["plan_id"] >= 1

    rw = body["runways"]
    # 同时刻按航班号稳定排序：CZ303 在 ZH404 前
    assert rw["18L"]["sequence"] == ["CA101", "MU202", "CZ303", "ZH404"]

    v = rw["18L"]["violations"]
    # HEAVY->MEDIUM 需 120s 实际 60s；MEDIUM->LIGHT 需 120s 实际 60s；LIGHT->HEAVY 需 60s 实际 0s
    assert [(x["leader"], x["follower"]) for x in v] == [
        ("CA101", "MU202"),
        ("MU202", "CZ303"),
        ("CZ303", "ZH404"),
    ]
    assert v[0]["required_seconds"] == 120 and v[0]["actual_seconds"] == 60
    assert v[0]["shortage_seconds"] == 60
    # 18R 间隔 120s >= 60s，无违规
    assert rw["18R"]["violations"] == []
    return body["plan_id"]


def test_trial_cascades_and_preserves_plan():
    plan_id = test_create_plan_and_violations()

    r = client.post(f"/api/v1/plans/{plan_id}/trial")
    assert r.status_code == 200, r.text
    items = r.json()["runways"]["18L"]
    by_no = {i["flight_no"]: i for i in items}

    # 首班不延误
    assert by_no["CA101"]["delay_seconds"] == 0
    assert by_no["CA101"]["triggered_by"] is None
    # MU202 推迟到 08:02:00（CA101 + 120s）
    assert "T08:02:00" in by_no["MU202"]["adjusted_time"]
    assert by_no["MU202"]["delay_seconds"] == 60
    assert by_no["MU202"]["triggered_by"] == "CA101"
    # 级联：CZ303 推迟到 08:04:00（MU202 调整后 + 120s），延误 120s
    assert "T08:04:00" in by_no["CZ303"]["adjusted_time"]
    assert by_no["CZ303"]["delay_seconds"] == 120
    assert by_no["CZ303"]["triggered_by"] == "MU202"
    # 级联：ZH404 推迟到 08:05:00（CZ303 调整后 + 60s），延误 180s
    assert "T08:05:00" in by_no["ZH404"]["adjusted_time"]
    assert by_no["ZH404"]["delay_seconds"] == 180
    assert by_no["ZH404"]["triggered_by"] == "CZ303"

    # 原计划不被改写
    plan = client.get(f"/api/v1/plans/{plan_id}").json()
    t18l = {f["flight_no"]: f["sched_time"] for f in plan["runways"]["18L"]}
    assert "T08:02:00" in t18l["ZH404"]
    # 试排后违规仍在（原始计划未变）
    again = client.get(f"/api/v1/plans/{plan_id}/violations").json()
    assert len(again["runways"]["18L"]["violations"]) == 3


def test_validation_errors():
    # 航班号重复
    dup = make_plan()
    dup["runways"]["18R"][0]["flight_no"] = "CA101"
    r = client.post("/api/v1/plans", json=dup)
    assert r.status_code == 422

    # 间隔矩阵缺少组合
    missing = make_plan()
    del missing["separation_matrix"]["HEAVY"]["LIGHT"]
    r = client.post("/api/v1/plans", json=missing)
    assert r.status_code == 422

    # 不存在的计划
    assert client.get("/api/v1/plans/9999/violations").status_code == 404
    assert client.post("/api/v1/plans/9999/trial").status_code == 404
