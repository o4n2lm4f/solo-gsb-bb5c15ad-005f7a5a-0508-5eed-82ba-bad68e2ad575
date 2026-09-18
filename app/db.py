"""SQLite 存取层。数据库路径由环境变量 DATABASE_PATH 指定，默认为项目根目录下 wakegap.db。"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "wakegap.db")
DB_PATH = os.environ.get("DATABASE_PATH", DEFAULT_DB)

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS separation_rules (
    plan_id           INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    leader_category   TEXT NOT NULL,
    follower_category TEXT NOT NULL,
    min_seconds       INTEGER NOT NULL CHECK (min_seconds >= 0),
    PRIMARY KEY (plan_id, leader_category, follower_category)
);
CREATE TABLE IF NOT EXISTS flights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    runway        TEXT NOT NULL,
    flight_no     TEXT NOT NULL,
    sched_time    TEXT NOT NULL,
    wake_category TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    UNIQUE (plan_id, flight_no)
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    if DB_PATH != ":memory:":
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def insert_plan(
    name: Optional[str],
    created_at: str,
    matrix: Dict[str, Dict[str, int]],
    runways: Dict[str, List[dict]],
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO plans (name, created_at) VALUES (?, ?)", (name, created_at)
        )
        plan_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO separation_rules (plan_id, leader_category, follower_category, min_seconds)"
            " VALUES (?, ?, ?, ?)",
            [
                (plan_id, leader, follower, seconds)
                for leader, row in matrix.items()
                for follower, seconds in row.items()
            ],
        )
        conn.executemany(
            "INSERT INTO flights (plan_id, runway, flight_no, sched_time, wake_category, seq)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (plan_id, runway, f["flight_no"], f["sched_time"], f["wake_category"], seq)
                for runway, flights in runways.items()
                for seq, f in enumerate(flights)
            ],
        )
    return plan_id


def load_plan(plan_id: int) -> Optional[Tuple[dict, Dict[Tuple[str, str], int], Dict[str, List[dict]]]]:
    """返回 (计划元信息, 间隔矩阵 {(前机,后机): 秒}, 跑道 -> 航班列表)；不存在返回 None。"""
    with get_conn() as conn:
        plan = conn.execute(
            "SELECT id, name, created_at FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if plan is None:
            return None
        rules = conn.execute(
            "SELECT leader_category, follower_category, min_seconds"
            " FROM separation_rules WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
        flights = conn.execute(
            "SELECT runway, flight_no, sched_time, wake_category"
            " FROM flights WHERE plan_id = ? ORDER BY runway, seq",
            (plan_id,),
        ).fetchall()

    matrix = {(r["leader_category"], r["follower_category"]): r["min_seconds"] for r in rules}
    runways: Dict[str, List[dict]] = {}
    for f in flights:
        runways.setdefault(f["runway"], []).append(
            {
                "flight_no": f["flight_no"],
                "sched_time": f["sched_time"],
                "wake_category": f["wake_category"],
            }
        )
    return dict(plan), matrix, runways
