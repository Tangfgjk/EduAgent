"""Trusted offline provisioning and explicit separate-port school instance startup."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.school.application import create_school_app
from app.school.identity import SchoolDirectory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="explicit isolated school instance directory")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision", help="offline administrator only; never exposed as HTTP registration")
    provision.add_argument("--tenant", required=True)
    provision.add_argument("--class-id", required=True)
    provision.add_argument("--username", required=True)
    provision.add_argument("--role", choices=("student", "teacher", "parent"), required=True)
    provision.add_argument("--learner-id")
    assign = commands.add_parser("assign", help="offline verified teacher/guardian association")
    assign.add_argument("--actor-id", required=True)
    assign.add_argument("--learner-id", required=True)
    assign.add_argument("--tenant", required=True)
    assign.add_argument("--class-id", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = SchoolDirectory(root / "school-directory.sqlite3")
    if args.command == "provision":
        password = getpass.getpass("Local account password (not echoed): ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            parser.error("passwords do not match")
        directory.provision_tenant(args.tenant)
        directory.provision_class(args.tenant, args.class_id)
        account_id = directory.provision_account(args.tenant, args.username, password, args.role, learner_id=args.learner_id)
        directory.membership(account_id, args.tenant, args.class_id)
        print("Provisioned account ID:", account_id)
        directory.close()
    elif args.command == "assign":
        directory.assign(args.actor_id, args.learner_id, args.tenant, args.class_id)
        print("Verified local assignment recorded.")
        directory.close()
    else:
        if args.port == 8000 or not 1024 <= args.port <= 65535:
            parser.error("choose separate non-8000 port between 1024 and 65535")
        import uvicorn
        app = create_school_app(directory, root / "learners")
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            app.state.learner_instances.close()
            directory.close()


if __name__ == "__main__":
    main()
