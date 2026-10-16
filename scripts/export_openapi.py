"""从正式网关导出 Schema，不连接数据库或加载真实模型/密钥。"""
import argparse
import json
from pathlib import Path

from app.config import Settings
from app.gateway.v1 import create_app
from app.infrastructure.db.database import Database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    database = Database("sqlite://")
    try:
        schema = create_app(Settings(), database).openapi()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        database.close()


if __name__ == "__main__":
    main()
