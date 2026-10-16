"""入口：python -m app.main 启动（默认 127.0.0.1:8000，RSI_PORT 可换端口）。"""
from __future__ import annotations

import os
import argparse
from pathlib import Path

import uvicorn

from app.config import Settings
from app.gateway.v1 import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="桂子问津 API")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    settings = Settings.load(args.env_file)
    port = int(os.getenv("RSI_PORT", "8000"))
    app = create_app(settings)
    print(f"桂子问津 | 账号与工作空间 API | http://127.0.0.1:{port}/docs")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    main()
