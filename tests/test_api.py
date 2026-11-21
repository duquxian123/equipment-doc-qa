from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
import hashlib
import shutil
import os
from datetime import date, timedelta
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
AUTH_TEMP = tempfile.TemporaryDirectory()
os.environ["EQUIPMENT_QA_DB_PATH"] = str(Path(AUTH_TEMP.name) / "test_app.db")
os.environ["EQUIPMENT_QA_OPS_DB_PATH"] = str(Path(AUTH_TEMP.name) / "test_ops.db")
sys.path.insert(0, str(ROOT / "src"))

import api  # noqa: E402
import drafts  # noqa: E402


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(api.app)
        status = cls.client.get("/auth/status").json()
        credentials = {"username": "test_admin", "password": "TestPassword123!"}
        if status["initialized"]:
            response = cls.client.post("/auth/login", json=credentials)
        else:
            response = cls.client.post("/auth/setup", json=credentials)
        if response.status_code != 200:
            raise AssertionError(response.text)

    @classmethod
    def tearDownClass(cls) -> None:
        AUTH_TEMP.cleanup()

    def test_health_and_documents(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ready_for_questions"])

        response = self.client.get("/documents")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["totals"]["documents"], 0)
        self.assertGreater(payload["totals"]["chunks"], 0)

    def test_web_and_pdf_are_served(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("问答工作台", page.text)

        document = self.client.get("/documents").json()["documents"][0]
        pdf = self.client.get(document["pdf_url"])
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")

    def test_citation_mapping(self) -> None:
        candidates = [
            {
                "id": "T-GXDSL-249-2025_切割设备维护保养规范#0001",
                "doc": "T-GXDSL-249-2025_切割设备维护保养规范",
                "page_from": 7,
                "page_to": 8,
                "section": "八、维护周期与计划管理",
            }
        ]
        answer = "每季度一次。［切割设备维护保养规范 第7-8页］"
        located = api.locate_citations(answer, candidates)
        self.assertEqual([item["id"] for item in located], [candidates[0]["id"]])

        refused = api.locate_citations("资料中没有相关内容。［切割设备维护保养规范 第7页］", candidates)
        self.assertEqual(refused, [])

    def test_invalid_upload_and_unknown_job(self) -> None:
        invalid = self.client.post(
            "/documents/upload",
            files={"file": ("notes.txt", b"not a pdf", "text/plain")},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get("/jobs/not-found").status_code, 404)

    def test_auth_and_role_permissions(self) -> None:
        anonymous = TestClient(api.app)
        self.assertEqual(anonymous.get("/documents").status_code, 401)

        users = self.client.get("/admin/users")
        self.assertEqual(users.status_code, 200)
        created = self.client.post(
            "/admin/users",
            json={"username": "normal_user", "password": "NormalPass123!", "role": "user"},
        )
        self.assertEqual(created.status_code, 201)

        normal = TestClient(api.app)
        login = normal.post(
            "/auth/login",
            json={"username": "normal_user", "password": "NormalPass123!"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(normal.get("/documents").status_code, 200)
        self.assertEqual(normal.get("/versions").status_code, 403)
        self.assertEqual(
            normal.post(
                "/documents/upload",
                files={"file": ("notes.txt", b"not a pdf", "text/plain")},
            ).status_code,
            403,
        )
        self.assertEqual(normal.get("/ops/dashboard").status_code, 200)
        self.assertEqual(
            normal.post(
                "/ops/orders",
                json={
                    "customer": "权限测试客户",
                    "alloy": "5052",
                    "thickness_mm": 0.5,
                    "quantity_tons": 20,
                    "due_date": (date.today() + timedelta(days=7)).isoformat(),
                    "priority": "normal",
                },
            ).status_code,
            403,
        )

    def test_ops_demo_dashboard_has_history_and_analysis(self) -> None:
        response = self.client.get("/ops/dashboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["order_counts"]["total"], 24)
        self.assertGreater(payload["production"]["qualified_tons"], 0)
        self.assertGreater(payload["inventory"]["finished_goods_tons"], 0)
        self.assertTrue(payload["defect_distribution"])
        self.assertGreaterEqual(len(payload["line_performance"]), 3)
        self.assertTrue(payload["production_trend"])
        self.assertIn("模拟数据", payload["disclaimer"])

    def test_ops_order_to_cost_and_inventory_flow(self) -> None:
        before = self.client.get("/ops/dashboard").json()
        created = self.client.post(
            "/ops/orders",
            json={
                "customer": "闭环测试客户",
                "alloy": "5052",
                "thickness_mm": 0.5,
                "quantity_tons": 20,
                "due_date": (date.today() + timedelta(days=7)).isoformat(),
                "priority": "rush",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        order = created.json()
        self.assertEqual(order["status"], "planned")
        self.assertEqual(order["planned_cost"], 370000.0)

        dispatched = self.client.post(
            f"/ops/orders/{order['id']}/dispatch",
            json={"line": "1#冷轧线"},
        )
        self.assertEqual(dispatched.status_code, 200, dispatched.text)
        dispatched_order = dispatched.json()
        self.assertEqual(dispatched_order["status"], "dispatched")
        self.assertTrue(dispatched_order["work_order_id"].startswith("WO-"))

        duplicate = self.client.post(
            f"/ops/orders/{order['id']}/dispatch",
            json={"line": "2#冷轧线"},
        )
        self.assertEqual(duplicate.status_code, 409)

        completed = self.client.post(
            f"/ops/work-orders/{dispatched_order['work_order_id']}/complete",
            json={
                "input_weight_kg": 21000,
                "qualified_weight_kg": 20000,
                "scrap_weight_kg": 500,
                "downtime_minutes": 30,
                "energy_kwh": 9000,
                "shift": "早班",
                "quality_result": "passed",
                "defect_type": None,
                "inspector": "测试质检员",
                "operator": "测试操作员",
                "notes": "订单到成本闭环测试",
            },
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        finished = completed.json()
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["yield_percent"], 95.24)
        self.assertEqual(finished["material_cost"], 388500.0)
        self.assertEqual(finished["energy_cost"], 6480.0)
        self.assertEqual(finished["downtime_cost"], 255.0)
        self.assertEqual(finished["total_cost"], 395235.0)
        self.assertEqual(finished["unit_cost_per_ton"], 19761.75)
        self.assertEqual(finished["cost_variance"], 25235.0)
        self.assertEqual(finished["quality_result"], "passed")

        duplicate_report = self.client.post(
            f"/ops/work-orders/{dispatched_order['work_order_id']}/complete",
            json={
                "input_weight_kg": 21000,
                "qualified_weight_kg": 20000,
                "scrap_weight_kg": 500,
                "downtime_minutes": 30,
                "energy_kwh": 9000,
                "shift": "早班",
                "quality_result": "passed",
                "defect_type": None,
                "inspector": "测试质检员",
                "operator": "测试操作员",
                "notes": "",
            },
        )
        self.assertEqual(duplicate_report.status_code, 409)

        after = self.client.get("/ops/dashboard").json()
        self.assertAlmostEqual(
            after["inventory"]["finished_goods_tons"],
            before["inventory"]["finished_goods_tons"] + 20.0,
            places=2,
        )
        self.assertAlmostEqual(
            after["inventory"]["scrap_tons"],
            before["inventory"]["scrap_tons"] + 0.5,
            places=2,
        )

    def test_draft_review_does_not_change_active_index(self) -> None:
        active_before = hashlib.sha256(api.CHUNKS_FILE.read_bytes()).hexdigest()
        job_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "draft-smoke.pdf"
            original = sorted((ROOT / "corpus").glob("*.pdf"))[0]
            document = pymupdf.open(original)
            document.set_metadata({"title": "Draft review smoke test", "author": "Codex"})
            document.save(source, garbage=3, deflate=True)
            document.close()
            content_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            drafts.create_draft(
                job_id,
                source_path=source,
                original_name="draft-smoke.pdf",
                content_hash=content_hash,
                document_name=f"draft-smoke-{job_id[:8]}",
            )
        try:
            processed = drafts.process_job(job_id)
            self.assertEqual(processed["status"], "review")
            self.assertGreater(processed["chunk_count"], 0)

            review = drafts.review_payload(job_id)
            self.assertTrue(review["cleaning"]["pages"])
            self.assertTrue(review["chunks"])
            first_chunk = review["chunks"][0]
            drafts.update_chunk(job_id, first_chunk["id"], {"section": "审核测试章节"})
            updated = drafts.review_payload(job_id)
            self.assertEqual(updated["chunks"][0]["section"], "审核测试章节")
            self.assertEqual(
                hashlib.sha256(api.CHUNKS_FILE.read_bytes()).hexdigest(),
                active_before,
            )
        finally:
            drafts.discard_draft(job_id)
            shutil.rmtree(drafts.job_dir(job_id), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
