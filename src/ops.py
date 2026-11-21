"""生产运营沙盘：模拟 ERP 订单、MES 工单、生产回报和成本结算。

该模块用于演示制造业数字化项目的一条完整业务闭环，不连接真实企业系统。
所有预置订单和生产数据均为程序生成的模拟数据。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import auth

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(
    os.environ.get("EQUIPMENT_QA_OPS_DB_PATH", str(ROOT / "output" / "ops.db"))
)

MATERIAL_PRICE_PER_TON = 18_500.0
ENERGY_PRICE_PER_KWH = 0.72
DOWNTIME_COST_PER_MINUTE = 8.5

LINES = ("1#冷轧线", "2#冷轧线", "精整线")
ALLOYS = ("5052", "3003", "1060")
DEFECTS = ("厚度超差", "表面划伤", "板形不良", "孔洞")

_db_lock = threading.Lock()
router = APIRouter(prefix="/ops", tags=["生产运营沙盘"])


class CreateOrderRequest(BaseModel):
    customer: str = Field(min_length=1, max_length=80, examples=["模拟客户·华东加工中心"])
    alloy: str = Field(min_length=1, max_length=20, examples=["5052"])
    thickness_mm: float = Field(gt=0, le=10, examples=[0.5])
    quantity_tons: float = Field(gt=0, le=200, examples=[20])
    due_date: str = Field(examples=["2026-11-20"])
    priority: Literal["normal", "rush"] = "normal"


class DispatchRequest(BaseModel):
    line: str = Field(min_length=1, max_length=40, examples=["1#冷轧线"])


class CompleteWorkOrderRequest(BaseModel):
    input_weight_kg: float = Field(gt=0, le=500_000)
    qualified_weight_kg: float = Field(ge=0, le=500_000)
    scrap_weight_kg: float = Field(ge=0, le=500_000)
    downtime_minutes: float = Field(ge=0, le=100_000)
    energy_kwh: float = Field(ge=0, le=10_000_000)
    shift: Literal["早班", "中班", "夜班"]
    quality_result: Literal["passed", "failed"]
    defect_type: str | None = Field(default=None, max_length=40)
    inspector: str = Field(min_length=1, max_length=40)
    operator: str = Field(min_length=1, max_length=40)
    notes: str = Field(default="", max_length=500)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize() -> None:
    with _db_lock, closing(_connect()) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ops_sales_orders (
                id TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                alloy TEXT NOT NULL,
                thickness_mm REAL NOT NULL,
                quantity_tons REAL NOT NULL,
                due_date TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                planned_cost REAL NOT NULL,
                line TEXT,
                defect_type TEXT,
                created_at TEXT NOT NULL,
                dispatched_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ops_work_orders (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE,
                line TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE TABLE IF NOT EXISTS ops_production_reports (
                id TEXT PRIMARY KEY,
                work_order_id TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL UNIQUE,
                input_weight_kg REAL NOT NULL,
                qualified_weight_kg REAL NOT NULL,
                scrap_weight_kg REAL NOT NULL,
                downtime_minutes REAL NOT NULL,
                energy_kwh REAL NOT NULL,
                shift TEXT NOT NULL,
                operator TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(work_order_id) REFERENCES ops_work_orders(id),
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE TABLE IF NOT EXISTS ops_quality_reports (
                id TEXT PRIMARY KEY,
                work_order_id TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL UNIQUE,
                result TEXT NOT NULL,
                defect_type TEXT,
                inspector TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(work_order_id) REFERENCES ops_work_orders(id),
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE TABLE IF NOT EXISTS ops_cost_snapshots (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE,
                material_cost REAL NOT NULL,
                energy_cost REAL NOT NULL,
                downtime_cost REAL NOT NULL,
                total_cost REAL NOT NULL,
                unit_cost_per_ton REAL NOT NULL,
                planned_cost REAL NOT NULL,
                cost_variance REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE TABLE IF NOT EXISTS ops_inventory_movements (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                movement_type TEXT NOT NULL,
                weight_kg REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE TABLE IF NOT EXISTS ops_integration_events (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES ops_sales_orders(id)
            );

            CREATE INDEX IF NOT EXISTS idx_ops_orders_created
                ON ops_sales_orders(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ops_work_orders_status
                ON ops_work_orders(status);
            """
        )
        count = connection.execute("SELECT COUNT(*) FROM ops_sales_orders").fetchone()[0]
        if count == 0:
            _seed_demo_locked(connection)


def _cost_values(
    input_weight_kg: float,
    qualified_weight_kg: float,
    downtime_minutes: float,
    energy_kwh: float,
    planned_cost: float,
) -> dict[str, float]:
    material_cost = round(input_weight_kg / 1000 * MATERIAL_PRICE_PER_TON, 2)
    energy_cost = round(energy_kwh * ENERGY_PRICE_PER_KWH, 2)
    downtime_cost = round(downtime_minutes * DOWNTIME_COST_PER_MINUTE, 2)
    total_cost = round(material_cost + energy_cost + downtime_cost, 2)
    unit_cost = (
        round(total_cost / (qualified_weight_kg / 1000), 2)
        if qualified_weight_kg > 0
        else 0.0
    )
    return {
        "material_cost": material_cost,
        "energy_cost": energy_cost,
        "downtime_cost": downtime_cost,
        "total_cost": total_cost,
        "unit_cost_per_ton": unit_cost,
        "planned_cost": round(planned_cost, 2),
        "cost_variance": round(total_cost - planned_cost, 2),
    }


def _seed_demo_locked(connection: sqlite3.Connection) -> None:
    """生成 24 笔历史订单，作为看板的模拟数据基线。"""
    now = datetime.now(timezone.utc)
    for index in range(24):
        sequence = index + 1
        order_id = f"SO-DEMO-{sequence:03d}"
        work_order_id = f"WO-DEMO-{sequence:03d}"
        alloy = ALLOYS[index % len(ALLOYS)]
        quantity_tons = round(18 + (index % 6) * 2.5, 1)
        thickness = round(0.35 + (index % 5) * 0.15, 2)
        line = LINES[index % len(LINES)]
        completed_at = now - timedelta(days=29 - index)
        created_at = completed_at - timedelta(days=2, hours=index % 6)
        dispatched_at = created_at + timedelta(hours=3)
        due_date = (completed_at + timedelta(days=4)).date().isoformat()
        planned_cost = round(quantity_tons * MATERIAL_PRICE_PER_TON, 2)

        input_weight = round(quantity_tons * 1000 * (1.035 + (index % 3) * 0.008), 1)
        qualified_weight = round(input_weight * (0.945 + (index % 7) * 0.006), 1)
        scrap_weight = round(input_weight * (0.014 + (index % 4) * 0.002), 1)
        downtime = float(25 + (index * 17) % 180)
        energy = round(input_weight * (0.43 + (index % 4) * 0.02), 1)
        shift = ("早班", "中班", "夜班")[index % 3]
        defect = None if index % 4 == 0 else DEFECTS[index % len(DEFECTS)]
        quality_result = "passed" if defect is None else "failed"
        costs = _cost_values(
            input_weight,
            qualified_weight,
            downtime,
            energy,
            planned_cost,
        )

        connection.execute(
            """
            INSERT INTO ops_sales_orders(
                id, customer, alloy, thickness_mm, quantity_tons, due_date,
                priority, status, planned_cost, line, defect_type, created_at,
                dispatched_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                f"模拟客户·{('华北','华东','华南','西南')[index % 4]}加工中心",
                alloy,
                thickness,
                quantity_tons,
                due_date,
                "rush" if index % 7 == 0 else "normal",
                planned_cost,
                line,
                defect,
                created_at.isoformat(),
                dispatched_at.isoformat(),
                completed_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_work_orders(id, order_id, line, status, created_at, completed_at)
            VALUES (?, ?, ?, 'completed', ?, ?)
            """,
            (work_order_id, order_id, line, created_at.isoformat(), completed_at.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO ops_production_reports(
                id, work_order_id, order_id, input_weight_kg, qualified_weight_kg,
                scrap_weight_kg, downtime_minutes, energy_kwh, shift, operator,
                notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"PR-DEMO-{sequence:03d}",
                work_order_id,
                order_id,
                input_weight,
                qualified_weight,
                scrap_weight,
                downtime,
                energy,
                shift,
                f"模拟操作员{index % 5 + 1}",
                "模拟历史报工",
                completed_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_quality_reports(
                id, work_order_id, order_id, result, defect_type, inspector, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"QC-DEMO-{sequence:03d}",
                work_order_id,
                order_id,
                quality_result,
                defect,
                f"模拟质检员{index % 3 + 1}",
                "模拟质量记录",
                completed_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_cost_snapshots(
                id, order_id, material_cost, energy_cost, downtime_cost,
                total_cost, unit_cost_per_ton, planned_cost, cost_variance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"COST-DEMO-{sequence:03d}",
                order_id,
                costs["material_cost"],
                costs["energy_cost"],
                costs["downtime_cost"],
                costs["total_cost"],
                costs["unit_cost_per_ton"],
                costs["planned_cost"],
                costs["cost_variance"],
                completed_at.isoformat(),
            ),
        )
        for movement_type, weight in (
            ("finished_goods", qualified_weight),
            ("scrap", scrap_weight),
        ):
            connection.execute(
                """
                INSERT INTO ops_inventory_movements(
                    id, order_id, sku, movement_type, weight_kg, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"INV-DEMO-{sequence:03d}-{movement_type}",
                    order_id,
                    f"{alloy}-{thickness:.2f}",
                    movement_type,
                    weight,
                    completed_at.isoformat(),
                ),
            )
        connection.execute(
            """
            INSERT INTO ops_integration_events(
                id, order_id, event_type, source, target, status, detail, created_at
            ) VALUES (?, ?, 'work_order_dispatch', 'ERP', 'MES', 'accepted', ?, ?)
            """,
            (
                f"INT-DEMO-{sequence:03d}-OUT",
                order_id,
                f"{work_order_id} 已生成",
                dispatched_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_integration_events(
                id, order_id, event_type, source, target, status, detail, created_at
            ) VALUES (?, ?, 'production_result', 'MES', 'ERP', 'processed', ?, ?)
            """,
            (
                f"INT-DEMO-{sequence:03d}-IN",
                order_id,
                "产量、质量与成本已回传",
                completed_at.isoformat(),
            ),
        )


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def _order_join_sql(where: str = "", order: str = "ORDER BY o.created_at DESC", limit: str = "") -> str:
    return f"""
        SELECT
            o.*,
            w.id AS work_order_id,
            w.status AS work_order_status,
            p.input_weight_kg,
            p.qualified_weight_kg,
            p.scrap_weight_kg,
            p.downtime_minutes,
            p.energy_kwh,
            p.shift,
            q.result AS quality_result,
            q.inspector,
            c.material_cost,
            c.energy_cost,
            c.downtime_cost,
            c.total_cost,
            c.unit_cost_per_ton,
            c.cost_variance
        FROM ops_sales_orders o
        LEFT JOIN ops_work_orders w ON w.order_id = o.id
        LEFT JOIN ops_production_reports p ON p.order_id = o.id
        LEFT JOIN ops_quality_reports q ON q.order_id = o.id
        LEFT JOIN ops_cost_snapshots c ON c.order_id = o.id
        {where}
        {order}
        {limit}
    """


def _order_detail_with_connection(
    connection: sqlite3.Connection,
    order_id: str,
) -> dict:
    row = connection.execute(
        _order_join_sql("WHERE o.id = ?", "", "LIMIT 1"),
        (order_id,),
    ).fetchone()
    if not row:
        raise FileNotFoundError("订单不存在")
    result = dict(row)
    if result.get("input_weight_kg"):
        result["yield_percent"] = round(
            result["qualified_weight_kg"] / result["input_weight_kg"] * 100,
            2,
        )
    else:
        result["yield_percent"] = None
    result["events"] = [
        dict(event)
        for event in connection.execute(
            """
            SELECT event_type, source, target, status, detail, created_at
            FROM ops_integration_events
            WHERE order_id = ?
            ORDER BY created_at DESC
            """,
            (order_id,),
        ).fetchall()
    ]
    return result


def list_orders(limit: int = 50) -> list[dict]:
    initialize()
    bounded = max(1, min(limit, 200))
    with closing(_connect()) as connection:
        rows = connection.execute(
            _order_join_sql(limit="LIMIT ?"),
            (bounded,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_order(order_id: str) -> dict:
    initialize()
    with closing(_connect()) as connection:
        return _order_detail_with_connection(connection, order_id)


def create_order(payload: dict) -> dict:
    initialize()
    try:
        due_date = date.fromisoformat(str(payload["due_date"]))
    except ValueError as exc:
        raise ValueError("交期必须使用 YYYY-MM-DD 格式") from exc

    order_id = _new_id("SO")
    now = utc_now()
    planned_cost = round(float(payload["quantity_tons"]) * MATERIAL_PRICE_PER_TON, 2)
    with _db_lock, closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO ops_sales_orders(
                id, customer, alloy, thickness_mm, quantity_tons, due_date,
                priority, status, planned_cost, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)
            """,
            (
                order_id,
                str(payload["customer"]).strip(),
                str(payload["alloy"]).strip(),
                float(payload["thickness_mm"]),
                float(payload["quantity_tons"]),
                due_date.isoformat(),
                payload.get("priority", "normal"),
                planned_cost,
                now,
            ),
        )
        return _order_detail_with_connection(connection, order_id)


def dispatch_order(order_id: str, line: str) -> dict:
    initialize()
    line = line.strip()
    if not line:
        raise ValueError("必须指定产线")

    with _db_lock, closing(_connect()) as connection, connection:
        order = connection.execute(
            "SELECT * FROM ops_sales_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if not order:
            raise FileNotFoundError("订单不存在")
        if order["status"] != "planned":
            raise ValueError("只有待下发订单可以生成生产工单")
        existing = connection.execute(
            "SELECT id FROM ops_work_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if existing:
            raise ValueError("该订单已经下发，不能重复生成工单")

        work_order_id = _new_id("WO")
        now = utc_now()
        connection.execute(
            """
            INSERT INTO ops_work_orders(id, order_id, line, status, created_at)
            VALUES (?, ?, ?, 'dispatched', ?)
            """,
            (work_order_id, order_id, line, now),
        )
        connection.execute(
            """
            UPDATE ops_sales_orders
            SET status = 'dispatched', line = ?, dispatched_at = ?
            WHERE id = ?
            """,
            (line, now, order_id),
        )
        connection.execute(
            """
            INSERT INTO ops_integration_events(
                id, order_id, event_type, source, target, status, detail, created_at
            ) VALUES (?, ?, 'work_order_dispatch', 'ERP', 'MES', 'accepted', ?, ?)
            """,
            (_new_id("INT"), order_id, f"{work_order_id} 已生成", now),
        )
        return _order_detail_with_connection(connection, order_id)


def complete_work_order(work_order_id: str, payload: dict) -> dict:
    initialize()
    input_weight = float(payload["input_weight_kg"])
    qualified_weight = float(payload["qualified_weight_kg"])
    scrap_weight = float(payload["scrap_weight_kg"])
    downtime = float(payload["downtime_minutes"])
    energy = float(payload["energy_kwh"])
    quality_result = str(payload["quality_result"])
    defect_type = (payload.get("defect_type") or "").strip() or None

    if qualified_weight + scrap_weight > input_weight + 0.01:
        raise ValueError("合格产出与废品重量之和不能超过投入重量")
    if quality_result == "failed" and not defect_type:
        raise ValueError("质检不合格时必须填写缺陷类型")
    if quality_result == "passed":
        defect_type = None

    with _db_lock, closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT
                w.*,
                o.quantity_tons,
                o.planned_cost,
                o.alloy,
                o.thickness_mm
            FROM ops_work_orders w
            JOIN ops_sales_orders o ON o.id = w.order_id
            WHERE w.id = ?
            """,
            (work_order_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("生产工单不存在")
        if row["status"] != "dispatched":
            raise ValueError("只有已下发工单可以提交生产与质检结果")

        if connection.execute(
            "SELECT 1 FROM ops_production_reports WHERE work_order_id = ?",
            (work_order_id,),
        ).fetchone():
            raise ValueError("该工单已经报工，不能重复提交")

        now = utc_now()
        costs = _cost_values(
            input_weight,
            qualified_weight,
            downtime,
            energy,
            float(row["planned_cost"]),
        )
        order_id = str(row["order_id"])
        connection.execute(
            """
            INSERT INTO ops_production_reports(
                id, work_order_id, order_id, input_weight_kg, qualified_weight_kg,
                scrap_weight_kg, downtime_minutes, energy_kwh, shift, operator,
                notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("PR"),
                work_order_id,
                order_id,
                input_weight,
                qualified_weight,
                scrap_weight,
                downtime,
                energy,
                payload["shift"],
                str(payload["operator"]).strip(),
                str(payload.get("notes") or "").strip(),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_quality_reports(
                id, work_order_id, order_id, result, defect_type, inspector, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("QC"),
                work_order_id,
                order_id,
                quality_result,
                defect_type,
                str(payload["inspector"]).strip(),
                str(payload.get("notes") or "").strip(),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_cost_snapshots(
                id, order_id, material_cost, energy_cost, downtime_cost,
                total_cost, unit_cost_per_ton, planned_cost, cost_variance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("COST"),
                order_id,
                costs["material_cost"],
                costs["energy_cost"],
                costs["downtime_cost"],
                costs["total_cost"],
                costs["unit_cost_per_ton"],
                costs["planned_cost"],
                costs["cost_variance"],
                now,
            ),
        )
        connection.execute(
            "UPDATE ops_work_orders SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, work_order_id),
        )
        connection.execute(
            """
            UPDATE ops_sales_orders
            SET status = 'completed', defect_type = ?, completed_at = ?
            WHERE id = ?
            """,
            (defect_type, now, order_id),
        )
        for movement_type, weight in (
            ("finished_goods", qualified_weight),
            ("scrap", scrap_weight),
        ):
            connection.execute(
                """
                INSERT INTO ops_inventory_movements(
                    id, order_id, sku, movement_type, weight_kg, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("INV"),
                    order_id,
                    f"{row['alloy']}-{float(row['thickness_mm']):.2f}",
                    movement_type,
                    weight,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO ops_integration_events(
                id, order_id, event_type, source, target, status, detail, created_at
            ) VALUES (?, ?, 'production_result', 'MES', 'ERP', 'processed', ?, ?)
            """,
            (
                _new_id("INT"),
                order_id,
                "产量、质量、库存与成本已回传",
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO ops_integration_events(
                id, order_id, event_type, source, target, status, detail, created_at
            ) VALUES (?, ?, 'inventory_and_cost_update', 'ERP', 'ERP', 'posted', ?, ?)
            """,
            (_new_id("INT"), order_id, "库存和成本已更新", now),
        )
        detail = _order_detail_with_connection(connection, order_id)
        detail["cost"] = costs
        return detail


def dashboard() -> dict:
    initialize()
    with closing(_connect()) as connection:
        counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM ops_sales_orders GROUP BY status"
            ).fetchall()
        }
        production = connection.execute(
            """
            SELECT
                COALESCE(SUM(input_weight_kg), 0) AS input_kg,
                COALESCE(SUM(qualified_weight_kg), 0) AS qualified_kg,
                COALESCE(SUM(scrap_weight_kg), 0) AS scrap_kg,
                COALESCE(SUM(downtime_minutes), 0) AS downtime_minutes,
                COALESCE(SUM(energy_kwh), 0) AS energy_kwh
            FROM ops_production_reports
            """
        ).fetchone()
        costs = connection.execute(
            """
            SELECT
                COALESCE(SUM(total_cost), 0) AS total_cost,
                COALESCE(SUM(planned_cost), 0) AS planned_cost,
                COALESCE(SUM(cost_variance), 0) AS cost_variance
            FROM ops_cost_snapshots
            """
        ).fetchone()
        inventory_rows = connection.execute(
            """
            SELECT movement_type, COALESCE(SUM(weight_kg), 0) AS weight_kg
            FROM ops_inventory_movements
            GROUP BY movement_type
            """
        ).fetchall()
        inventory = {row["movement_type"]: round(row["weight_kg"], 1) for row in inventory_rows}

        defect_rows = connection.execute(
            """
            SELECT
                q.defect_type,
                COUNT(*) AS order_count,
                ROUND(COALESCE(SUM(p.scrap_weight_kg), 0), 1) AS scrap_weight_kg
            FROM ops_quality_reports q
            LEFT JOIN ops_production_reports p ON p.order_id = q.order_id
            WHERE q.result = 'failed' AND q.defect_type IS NOT NULL
            GROUP BY q.defect_type
            ORDER BY order_count DESC
            """
        ).fetchall()
        line_rows = connection.execute(
            """
            SELECT
                w.line,
                COUNT(*) AS order_count,
                ROUND(SUM(p.qualified_weight_kg) / 1000.0, 2) AS output_tons,
                ROUND(AVG(p.qualified_weight_kg * 100.0 / p.input_weight_kg), 2) AS yield_percent,
                ROUND(AVG(p.downtime_minutes), 1) AS avg_downtime_minutes
            FROM ops_work_orders w
            JOIN ops_production_reports p ON p.work_order_id = w.id
            WHERE w.status = 'completed'
            GROUP BY w.line
            ORDER BY w.line
            """
        ).fetchall()
        trend_rows = connection.execute(
            """
            SELECT
                substr(p.created_at, 1, 10) AS production_date,
                ROUND(SUM(p.qualified_weight_kg) / 1000.0, 2) AS output_tons,
                ROUND(AVG(p.qualified_weight_kg * 100.0 / p.input_weight_kg), 2) AS yield_percent
            FROM ops_production_reports p
            GROUP BY substr(p.created_at, 1, 10)
            ORDER BY production_date DESC
            LIMIT 14
            """
        ).fetchall()

        input_kg = float(production["input_kg"])
        qualified_kg = float(production["qualified_kg"])
        overall_yield = qualified_kg / input_kg * 100 if input_kg else 0.0
        avg_unit_cost = (
            float(costs["total_cost"]) / (qualified_kg / 1000)
            if qualified_kg
            else 0.0
        )
        recent_orders = [
            dict(row)
            for row in connection.execute(
                _order_join_sql(limit="LIMIT 8")
            ).fetchall()
        ]

    return {
        "disclaimer": "所有订单、生产、质量和成本数据均为程序生成的模拟数据，不代表真实企业经历。",
        "generated_at": utc_now(),
        "order_counts": {
            "total": sum(counts.values()),
            "planned": counts.get("planned", 0),
            "dispatched": counts.get("dispatched", 0),
            "completed": counts.get("completed", 0),
        },
        "production": {
            "input_tons": round(input_kg / 1000, 2),
            "qualified_tons": round(qualified_kg / 1000, 2),
            "scrap_tons": round(float(production["scrap_kg"]) / 1000, 2),
            "yield_percent": round(overall_yield, 2),
            "downtime_hours": round(float(production["downtime_minutes"]) / 60, 1),
            "energy_kwh": round(float(production["energy_kwh"]), 1),
            "avg_unit_cost": round(avg_unit_cost, 2),
            "cost_variance": round(float(costs["cost_variance"]), 2),
        },
        "inventory": {
            "finished_goods_tons": round(inventory.get("finished_goods", 0.0) / 1000, 2),
            "scrap_tons": round(inventory.get("scrap", 0.0) / 1000, 2),
        },
        "defect_distribution": [dict(row) for row in defect_rows],
        "line_performance": [dict(row) for row in line_rows],
        "production_trend": [dict(row) for row in reversed(trend_rows)],
        "recent_orders": recent_orders,
    }


@router.get("/dashboard")
def ops_dashboard() -> dict:
    return dashboard()


@router.get("/orders")
def ops_orders(limit: int = 50) -> dict:
    return {"orders": list_orders(limit)}


@router.get("/orders/{order_id}")
def ops_order(order_id: str) -> dict:
    try:
        return get_order(order_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/orders", status_code=201)
def ops_create_order(payload: CreateOrderRequest, request: Request) -> dict:
    try:
        result = create_order(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "ops.order_create",
        target=f"order:{result['id']}",
        details={
            "customer": result["customer"],
            "alloy": result["alloy"],
            "quantity_tons": result["quantity_tons"],
        },
        ip_address=request.client.host if request.client else "",
    )
    return result


@router.post("/orders/{order_id}/dispatch")
def ops_dispatch_order(
    order_id: str,
    payload: DispatchRequest,
    request: Request,
) -> dict:
    try:
        result = dispatch_order(order_id, payload.line)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "ops.order_dispatch",
        target=f"order:{order_id}",
        details={"work_order_id": result["work_order_id"], "line": payload.line},
        ip_address=request.client.host if request.client else "",
    )
    return result


@router.post("/work-orders/{work_order_id}/complete")
def ops_complete_work_order(
    work_order_id: str,
    payload: CompleteWorkOrderRequest,
    request: Request,
) -> dict:
    try:
        result = complete_work_order(work_order_id, payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    auth.audit(
        request.state.user,
        "ops.work_order_complete",
        target=f"work_order:{work_order_id}",
        details={
            "order_id": result["id"],
            "qualified_weight_kg": payload.qualified_weight_kg,
            "quality_result": payload.quality_result,
            "defect_type": result.get("defect_type"),
            "total_cost": result["cost"]["total_cost"],
        },
        ip_address=request.client.host if request.client else "",
    )
    return result
