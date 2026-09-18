"""Pydantic 请求/响应模型与输入校验。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def to_utc(dt: datetime) -> datetime:
    """统一时间：naive 视为 UTC，aware 转换为 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class FlightIn(BaseModel):
    flight_no: str = Field(min_length=1, max_length=16, description="唯一航班号")
    sched_time: datetime = Field(description="计划离场时刻，ISO 8601")
    wake_category: str = Field(min_length=1, max_length=16, description="尾流等级，如 HEAVY/MEDIUM/LIGHT")

    @field_validator("flight_no")
    @classmethod
    def _strip_no(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("flight_no 不能为空")
        return v

    @field_validator("wake_category")
    @classmethod
    def _norm_cat(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("wake_category 不能为空")
        return v

    @field_validator("sched_time")
    @classmethod
    def _norm_time(cls, v: datetime) -> datetime:
        return to_utc(v)


class PlanIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    separation_matrix: Dict[str, Dict[str, int]] = Field(
        description="前机等级 -> 后机等级 -> 最小间隔（秒）"
    )
    runways: Dict[str, List[FlightIn]] = Field(description="跑道号 -> 航班列表")

    @field_validator("separation_matrix")
    @classmethod
    def _norm_matrix(cls, m: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
        if not m:
            raise ValueError("separation_matrix 不能为空")
        norm: Dict[str, Dict[str, int]] = {}
        for leader, row in m.items():
            leader_key = leader.strip().upper()
            if not leader_key or not isinstance(row, dict) or not row:
                raise ValueError("间隔矩阵的行键与内容不能为空")
            norm_row: Dict[str, int] = {}
            for follower, seconds in row.items():
                follower_key = follower.strip().upper()
                if not follower_key:
                    raise ValueError("间隔矩阵的列键不能为空")
                if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
                    raise ValueError(f"间隔 [{leader_key}][{follower_key}] 必须为非负整数秒")
                norm_row[follower_key] = seconds
            norm[leader_key] = norm_row
        return norm

    @model_validator(mode="after")
    def _check_plan(self) -> "PlanIn":
        if not self.runways:
            raise ValueError("runways 不能为空，至少包含一条跑道")

        seen: set[str] = set()
        categories: set[str] = set()
        for runway, flights in self.runways.items():
            if not runway.strip():
                raise ValueError("跑道号不能为空")
            for f in flights:
                if f.flight_no in seen:
                    raise ValueError(f"航班号重复: {f.flight_no}")
                seen.add(f.flight_no)
                categories.add(f.wake_category)

        # 间隔矩阵必须覆盖所有实际出现的等级组合
        missing = [
            f"[{a}][{b}]"
            for a in categories
            for b in categories
            if b not in self.separation_matrix.get(a, {})
        ]
        if missing:
            raise ValueError("间隔矩阵缺少等级组合: " + ", ".join(sorted(missing)))
        return self


class Violation(BaseModel):
    runway: str
    leader: str
    follower: str
    leader_category: str
    follower_category: str
    required_seconds: int
    actual_seconds: int
    shortage_seconds: int


class RunwayViolations(BaseModel):
    sequence: List[str] = Field(description="按计划时刻+航班号稳定排序后的航班顺序")
    violations: List[Violation]


class PlanCreated(BaseModel):
    plan_id: int
    runways: Dict[str, RunwayViolations]


class PlanDetail(BaseModel):
    plan_id: int
    name: Optional[str]
    created_at: datetime
    separation_matrix: Dict[str, Dict[str, int]]
    runways: Dict[str, List[FlightIn]]


class TrialItem(BaseModel):
    flight_no: str
    wake_category: str
    sched_time: datetime = Field(description="原计划时刻（不改写）")
    adjusted_time: datetime = Field(description="调整后时刻（最早安全时刻）")
    delay_seconds: int
    triggered_by: Optional[str] = Field(description="触发延误的前机航班号，无延误时为 null")


class TrialResult(BaseModel):
    plan_id: int
    runways: Dict[str, List[TrialItem]]
    total_delay_seconds: int
