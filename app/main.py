"""跑道离场尾流间隔校验 API。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException

from . import db, logic
from .models import (
    FlightIn,
    PlanCreated,
    PlanDetail,
    PlanIn,
    RunwayViolations,
    TrialItem,
    TrialResult,
    Violation,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="跑道离场尾流间隔校验 API",
    version="1.0.0",
    lifespan=lifespan,
)


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_plan_or_404(plan_id: int):
    loaded = db.load_plan(plan_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"计划 {plan_id} 不存在")
    plan, matrix, runways = loaded
    # 数据库中的 ISO 字符串解析回 datetime，供排序与计算使用
    parsed = {
        runway: [
            {
                "flight_no": f["flight_no"],
                "sched_time": _parse_time(f["sched_time"]),
                "wake_category": f["wake_category"],
            }
            for f in flights
        ]
        for runway, flights in runways.items()
    }
    return plan, matrix, parsed


def _violations_by_runway(
    runways: Dict[str, list], matrix: Dict[Tuple[str, str], int]
) -> Dict[str, RunwayViolations]:
    result: Dict[str, RunwayViolations] = {}
    for runway, flights in runways.items():
        ordered = logic.sort_flights(flights)
        violations = [
            Violation(runway=runway, **v)
            for v in logic.compute_violations(ordered, matrix)
        ]
        result[runway] = RunwayViolations(
            sequence=[f["flight_no"] for f in ordered], violations=violations
        )
    return result


@app.post("/api/v1/plans", response_model=PlanCreated, status_code=201)
def create_plan(payload: PlanIn) -> PlanCreated:
    """提交按跑道分组的航班计划与间隔矩阵，返回计划 ID 及相邻航班间隔违规。"""
    runways = {
        runway: [
            {
                "flight_no": f.flight_no,
                "sched_time": f.sched_time.isoformat(),
                "wake_category": f.wake_category,
            }
            for f in flights
        ]
        for runway, flights in payload.runways.items()
    }
    plan_id = db.insert_plan(
        payload.name,
        datetime.now(timezone.utc).isoformat(),
        payload.separation_matrix,
        runways,
    )
    _, matrix, parsed = _get_plan_or_404(plan_id)
    return PlanCreated(plan_id=plan_id, runways=_violations_by_runway(parsed, matrix))


@app.get("/api/v1/plans/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: int) -> PlanDetail:
    """读取已存储的原始计划。"""
    plan, matrix, runways = _get_plan_or_404(plan_id)
    matrix_nested: Dict[str, Dict[str, int]] = {}
    for (leader, follower), seconds in matrix.items():
        matrix_nested.setdefault(leader, {})[follower] = seconds
    return PlanDetail(
        plan_id=plan["id"],
        name=plan["name"],
        created_at=_parse_time(plan["created_at"]),
        separation_matrix=matrix_nested,
        runways={
            runway: [FlightIn(**f) for f in flights] for runway, flights in runways.items()
        },
    )


@app.get("/api/v1/plans/{plan_id}/violations", response_model=PlanCreated)
def get_violations(plan_id: int) -> PlanCreated:
    """按计划时刻+航班号稳定排序，列出各跑道相邻航班的间隔违规。"""
    _, matrix, runways = _get_plan_or_404(plan_id)
    return PlanCreated(plan_id=plan_id, runways=_violations_by_runway(runways, matrix))


@app.post("/api/v1/plans/{plan_id}/trial", response_model=TrialResult)
def trial(plan_id: int) -> TrialResult:
    """试排：保持原顺序，逐项推迟至最早安全时刻，延误向后级联，不改写原计划。"""
    _, matrix, runways = _get_plan_or_404(plan_id)
    result: Dict[str, list] = {}
    total_delay = 0
    for runway, flights in runways.items():
        ordered = logic.sort_flights(flights)
        items = [TrialItem(**item) for item in logic.trial_schedule(ordered, matrix)]
        total_delay += sum(item.delay_seconds for item in items)
        result[runway] = items
    return TrialResult(plan_id=plan_id, runways=result, total_delay_seconds=total_delay)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
