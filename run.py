"""End-to-end pipeline runner: verify -> ingest -> filter -> score -> publish."""
from __future__ import annotations

import sys
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pipeline import filter as filter_step
from pipeline import ingest, publish, score, verify_sources

STEPS = [
    ("verify_sources", verify_sources.main),
    ("ingest", ingest.main),
    ("filter", filter_step.main),
    ("score", score.main),
    ("publish", publish.main),
]


def banner(text: str) -> None:
    print()
    print("#" * 72)
    print(f"#  {text}")
    print("#" * 72)


def main() -> int:
    failures: list[tuple[str, str]] = []
    started = time.monotonic()

    for idx, (name, fn) in enumerate(STEPS, start=1):
        banner(f"STAP {idx}/{len(STEPS)}: {name}")
        try:
            rc = fn()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if code not in (0, None):
                failures.append((name, f"sys.exit({code})"))
            continue
        except Exception as e:  # noqa: BLE001
            failures.append((name, f"{type(e).__name__}: {e}"))
            traceback.print_exc()
            continue
        if rc not in (0, None):
            failures.append((name, f"exit code {rc}"))

    elapsed = time.monotonic() - started
    print()
    print("=" * 72)
    print(f"END-TO-END KLAAR  ({elapsed:.1f}s)")
    print("=" * 72)
    if failures:
        print(f"{len(failures)} stap(pen) hadden problemen:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1
    print("Alle stappen succesvol.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
