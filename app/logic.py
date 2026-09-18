"""核心业务逻辑：稳定排序、间隔违规检测、保序试排（延误向后级联）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

Matrix = Dict[Tuple[str, str], int]


def sort_flights(flights: List[dict]) -> List[dict]:
    """按计划离场时刻升序、时刻相同按航班号字典序，保证确定性。"""
    return sorted(flights, key=lambda f: (f["sched_time"], f["flight_no"]))


def compute_violations(sorted_flights: List[dict], matrix: Matrix) -> List[dict]:
    """检查相邻航班（同一跑道）的实际间隔是否满足前机-后机最小间隔。"""
    violations: List[dict] = []
    for leader, follower in zip(sorted_flights, sorted_flights[1:]):
        required = matrix[(leader["wake_category"], follower["wake_category"])]
        actual = int((follower["sched_time"] - leader["sched_time"]).total_seconds())
        if actual < required:
            violations.append(
                {
                    "leader": leader["flight_no"],
                    "follower": follower["flight_no"],
                    "leader_category": leader["wake_category"],
                    "follower_category": follower["wake_category"],
                    "required_seconds": required,
                    "actual_seconds": actual,
                    "shortage_seconds": required - actual,
                }
            )
    return violations


def trial_schedule(sorted_flights: List[dict], matrix: Matrix) -> List[dict]:
    """保持原顺序，逐项推迟到最早安全时刻；延误沿队列向后级联，原计划时刻不改写。"""
    result: List[dict] = []
    prev: Optional[dict] = None
    for flight in sorted_flights:
        sched: datetime = flight["sched_time"]
        adjusted = sched
        triggered_by: Optional[str] = None
        if prev is not None:
            required = matrix[(prev["wake_category"], flight["wake_category"])]
            earliest = prev["adjusted_time"] + timedelta(seconds=required)
            if adjusted < earliest:
                adjusted = earliest
                triggered_by = prev["flight_no"]
        item = {
            "flight_no": flight["flight_no"],
            "wake_category": flight["wake_category"],
            "sched_time": sched,
            "adjusted_time": adjusted,
            "delay_seconds": int((adjusted - sched).total_seconds()),
            "triggered_by": triggered_by,
        }
        result.append(item)
        prev = {"flight_no": flight["flight_no"], "wake_category": flight["wake_category"],
                "adjusted_time": adjusted}
    return result
