"""pytest 公共夹具。

关键点：在导入应用前把数据库指向临时文件，保证测试不污染开发库。
"""

from __future__ import annotations

import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="trackcompass_test_"))
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LLM_PROVIDER"] = "rule"
os.environ["EMBEDDING_BACKEND"] = "local"
os.environ["VECTOR_BACKEND"] = "memory"
os.environ["GRAPH_BACKEND"] = "memory"
# RAG 固定走自建实现：测试必须自洽，不能依赖外部 RAGFlow 是否在线
os.environ["RAG_BACKEND"] = "memory"
os.environ["RAGFLOW_API_KEY"] = ""
os.environ["RAGFLOW_DATASET_ID"] = ""
os.environ["DEBUG"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.seed import DEMO_EMAIL, DEMO_PASSWORD, seed_all  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    init_db()
    db = SessionLocal()
    try:
        seed_all(db, reset=True)
        from app.services.analysis import refresh_graph, reset_and_reindex

        reset_and_reindex(db)
        refresh_graph(db)
    finally:
        db.close()
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login",
                       json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def tenant_id(client: TestClient, auth_headers: dict[str, str]) -> str:
    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    return resp.json()["data"]["tenant_id"]
