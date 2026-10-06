"""应用配置：环境变量优先，.env 兜底（docs/01 §7、platform/.env.example）。"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载：不覆盖已存在的真实环境变量。"""
    for key, value in _read_dotenv(path).items():
        os.environ.setdefault(key, value)


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read file values without mutating process-wide environment."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


@dataclass
class Settings:
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"
    db_path: str = "./data/rsi.sqlite3"
    learner_id: str = "demo_student_001"
    llm_extra_body_json: str = ""   # 透传 chat/completions 的私有字段（JSON 串），如关闭思考链
    local_teacher_token: str = ""
    local_parent_token: str = ""
    local_auth_enabled: bool = False
    local_auth_password_hash: str = ""
    local_auth_session_hours: int = 8
    local_auth_cookie_secure: bool = False
    allow_simulated_time: bool = False
    learning_catalog_path: str = ""
    learning_catalog_overlay_paths: tuple[str, ...] = ()

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        if env_file is None:
            env_file = Path(__file__).resolve().parent.parent / ".env"
        # Explicit process environment wins, then .env.local, then .env.
        file_values = _read_dotenv(env_file)
        file_values.update(_read_dotenv(env_file.with_name(".env.local")))
        def value(key: str, default: str) -> str:
            return os.environ.get(key, file_values.get(key, default))

        overlay_paths = json.loads(value("RSI_LEARNING_CATALOG_OVERLAYS", "[]"))
        if not isinstance(overlay_paths, list) or any(not isinstance(path, str) or not path for path in overlay_paths):
            raise ValueError("RSI_LEARNING_CATALOG_OVERLAYS must be a JSON array of nonempty paths")
        return cls(
            llm_base_url=value("RSI_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=value("RSI_LLM_API_KEY", cls.llm_api_key),
            llm_model=value("RSI_LLM_MODEL", cls.llm_model),
            db_path=value("RSI_DB_PATH", cls.db_path),
            learner_id=value("RSI_LEARNER_ID", cls.learner_id),
            llm_extra_body_json=value("RSI_LLM_EXTRA_BODY", cls.llm_extra_body_json),
            local_teacher_token=value("RSI_LOCAL_TEACHER_TOKEN", ""),
            local_parent_token=value("RSI_LOCAL_PARENT_TOKEN", ""),
            local_auth_enabled=value("RSI_LOCAL_AUTH_ENABLED", "false").lower() == "true",
            local_auth_password_hash=value("RSI_LOCAL_AUTH_PASSWORD_HASH", ""),
            local_auth_session_hours=int(value("RSI_LOCAL_AUTH_SESSION_HOURS", "8")),
            local_auth_cookie_secure=value("RSI_LOCAL_AUTH_COOKIE_SECURE", "false").lower() == "true",
            learning_catalog_path=value("RSI_LEARNING_CATALOG_PATH", ""),
            learning_catalog_overlay_paths=tuple(overlay_paths),
        )
