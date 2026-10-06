"""Build or check a pending course review package; never fabricate approval."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.learning.course_draft import build_equation_course_draft
from app.learning.course_governance import CourseReviewPackage, admit_course
from app.learning.pilot_readiness import PilotProtocol, pilot_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, help="write the pending synthetic bundle to this path")
    parser.add_argument("--check", type=Path, help="check a saved bundle; human approval remains unverified")
    parser.add_argument("--pilot-check", type=Path, help="check a pending pilot protocol without fabricating approvals")
    arguments = parser.parse_args()
    if sum(bool(value) for value in (arguments.build, arguments.check, arguments.pilot_check)) != 1:
        parser.error("choose exactly one of --build, --check or --pilot-check")
    if arguments.pilot_check:
        protocol = PilotProtocol.load(arguments.pilot_check)
        print(pilot_readiness(protocol, as_of=datetime.now(timezone.utc)).model_dump_json(indent=2))
        return 2
    if arguments.build:
        package = build_equation_course_draft()
        report = admit_course(package, as_of=datetime.now(timezone.utc))
        if not report.simulation_admitted:
            print(report.model_dump_json(indent=2))
            return 1
        if arguments.build.exists():
            parser.error("output already exists; use a new version path rather than overwrite an audited bundle")
        arguments.build.parent.mkdir(parents=True, exist_ok=True)
        arguments.build.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    else:
        package = CourseReviewPackage.load(arguments.check)
        report = admit_course(package, as_of=datetime.now(timezone.utc))
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.simulation_admitted else 1


if __name__ == "__main__":
    raise SystemExit(main())
