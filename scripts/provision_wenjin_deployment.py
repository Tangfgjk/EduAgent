"""Rotate the restored administrator and create a separate deployment audit account."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if os.name != "posix" or "--apply" not in sys.argv:
        raise RuntimeError("Use --apply on the restored deployment only")
    from deeptutor.multi_user.identity import get_user, save_user, set_password
    from deeptutor.services.auth import hash_password

    admin = os.environ.get("WENJIN_DEPLOY_ADMIN_USERNAME", "tzk")
    password = os.environ.get("WENJIN_DEPLOY_ADMIN_PASSWORD", "")
    audit_password = os.environ.get("WENJIN_DEPLOY_AUDIT_PASSWORD", "")
    if min(len(password), len(audit_password)) < 12:
        raise ValueError("Production passwords must have at least 12 characters")
    existing = get_user(admin)
    if not existing or existing["role"] != "admin":
        raise RuntimeError("Expected restored administrator was not found")
    set_password(admin, hash_password(password))
    save_user("wenjin_audit", hash_password(audit_password), role="user", preset="learner")
    print(json.dumps({
        "administrator": admin, "audit_account": "wenjin_audit",
        "audit_role": "user", "passwords_printed": False,
    }))


if __name__ == "__main__":
    main()
