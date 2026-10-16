"""本机维护入口；密码只使用 getpass，连接配置不回显。"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from app.config import Settings
from app.infrastructure.db.database import Database
from app.infrastructure.db.maintenance import backup, import_legacy, restore
from app.modules.errors import DomainError
from app.modules.identity.service import Identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    materialize = sub.add_parser("backfill-learning-facts", help="停服后核对旧 S05 运行；默认仅报告")
    materialize.add_argument("--apply", action="store_true")
    init = sub.add_parser("bootstrap-admin")
    init.add_argument("--username", required=True)
    init.add_argument("--display-name", default="管理员")
    export = sub.add_parser("backup")
    export.add_argument("--output", type=Path, required=True)
    recovery = sub.add_parser("restore")
    recovery.add_argument("--input", type=Path, required=True)
    legacy = sub.add_parser("import-legacy")
    legacy.add_argument("--source", type=Path, required=True)
    legacy.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.load(args.env_file)
    if not settings.database_url:
        parser.error("请配置 RSI_DATABASE_URL，或使用 --env-file .local/database.env")
    db = Database(settings.database_url)
    try:
        if args.command == "migrate":
            db.migrate()
            print("数据库迁移完成")
        else:
            db.assert_ready()
            if args.command == "bootstrap-admin":
                password = getpass.getpass("管理员密码（12–128 字符）：")
                if password != getpass.getpass("再次输入密码："):
                    parser.error("两次密码不一致")
                if not args.display_name.strip() or len(args.display_name.strip()) > 80:
                    parser.error("管理员名称须为 1–80 字")
                Identity(db).bootstrap(args.username, password, args.display_name.strip())
                print("首个管理员已初始化")
            elif args.command == "backup":
                print(json.dumps(backup(db, args.output), ensure_ascii=False))
            elif args.command == "restore":
                print(json.dumps(restore(db, args.input), ensure_ascii=False))
            elif args.command == "backfill-learning-facts":
                from app.modules.memory.backfill import backfill
                print(json.dumps(backfill(db, args.apply), ensure_ascii=False))
            else:
                print(json.dumps(import_legacy(db, args.source, args.snapshot), ensure_ascii=False))
    except DomainError as error:
        parser.exit(1, error.message + "\n")
    except Exception:
        # SQL 异常/连接错误可能含凭据或学生原文，不原样打印维护命令堆栈。
        parser.exit(1, "维护操作失败；请检查连接、版本及目标库状态。原始异常未回显。\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
