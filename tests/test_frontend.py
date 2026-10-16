from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.v1 import create_app
from app.infrastructure.db.database import Database


def test_separate_frontend_entries_and_deep_links(tmp_path):
    for name in ("student", "admin"):
        folder = tmp_path / name
        (folder / "assets").mkdir(parents=True)
        (folder / "index.html").write_text(f"<title>{name}</title>", encoding="utf-8")
        (folder / "assets" / "app.js").write_text(f"console.log('{name}')", encoding="utf-8")
    database = Database("sqlite://")
    database.migrate()
    try:
        with TestClient(create_app(Settings(frontend_dir=str(tmp_path)), database)) as client:
            for name in ("student", "admin"):
                assert client.get(f"/{name}/").text == f"<title>{name}</title>"
                assert client.get(f"/{name}/spaces/deep/link").text == f"<title>{name}</title>"
                assert client.get(f"/{name}/assets/app.js").status_code == 200
                assert client.get(f"/{name}/assets/missing.js").status_code == 404
            assert client.get("/student/../app/config.py").status_code == 404
            assert client.get("/api/v1/auth/me").status_code == 401
    finally:
        database.close()
