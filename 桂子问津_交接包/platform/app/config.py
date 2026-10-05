"""应用配置：环境变量优先，.env 兜底（docs/01 §7、platform/.env.example）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载：不覆盖已存在的真实环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Settings:
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"
    db_path: str = "./data/rsi.sqlite3"
    learner_id: str = "demo_student_001"
    llm_extra_body_json: str = ""   # 透传 chat/completions 的私有字段（JSON 串），如关闭思考链

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        if env_file is None:
            env_file = Path(__file__).resolve().parent.parent / ".env"
        _load_dotenv(env_file)
        return cls(
            llm_base_url=os.getenv("RSI_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.getenv("RSI_LLM_API_KEY", cls.llm_api_key),
            llm_model=os.getenv("RSI_LLM_MODEL", cls.llm_model),
            db_path=os.getenv("RSI_DB_PATH", cls.db_path),
            learner_id=os.getenv("RSI_LEARNER_ID", cls.learner_id),
            llm_extra_body_json=os.getenv("RSI_LLM_EXTRA_BODY", cls.llm_extra_body_json),
        )
