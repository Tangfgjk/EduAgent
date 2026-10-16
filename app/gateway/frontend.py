"""独立静态输出；深层页面回退各自入口，缺失静态资源仍返回 404。"""
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class SPAFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code == 404 and not Path(path).suffix and not path.startswith("assets/"):
                return await super().get_response("index.html", scope)
            raise


def mount_frontends(app: FastAPI, root: Path):
    for name in ("student", "admin"):
        folder = root / name
        if (folder / "index.html").is_file():
            app.mount("/" + name, SPAFiles(directory=folder, html=True), name=name + "-frontend")
