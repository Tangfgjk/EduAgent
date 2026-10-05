"""入口：python -m app.main 启动（默认 127.0.0.1:8000，RSI_PORT 可换端口）。"""
from __future__ import annotations

import os

import uvicorn

from app.config import Settings
from app.gateway.routes import create_app


def main() -> None:
    settings = Settings.load()
    port = int(os.getenv("RSI_PORT", "8000"))
    app = create_app(settings)
    llm_kind = "GLM(OpenAI兼容)" if settings.llm_api_key else "FakeLLM（未配置 API Key）"
    print(f"RSI 教育智能体平台 v1 | LLM: {llm_kind} | http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
