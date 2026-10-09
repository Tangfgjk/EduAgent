"""Wenjin entry point; WENJIN_PORT (legacy RSI_PORT) selects loopback port."""
from __future__ import annotations

import uvicorn

from app.config import Settings
from app.gateway.routes import create_app


def main() -> None:
    settings = Settings.load()
    port = settings.port
    app = create_app(settings)
    llm_kind = f"外部模型 {settings.llm_model}（OpenAI 兼容）" if settings.llm_api_key else "FakeLLM（未配置 API Key）"
    print(f"桂子问津 Wenjin | LLM: {llm_kind} | http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
