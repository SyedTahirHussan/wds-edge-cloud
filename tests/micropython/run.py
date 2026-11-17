"""Run core_tests.py without pytest (used under the MicroPython Unix port).

Usage, from the repository root:  micropython tests/micropython/run.py
Exits with status 1 if any test fails.
"""

import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests/micropython")

import core_tests

failed = 0
names = sorted(n for n in dir(core_tests) if n.startswith("test_"))
for name in names:
    try:
        getattr(core_tests, name)()
        print("PASS", name)
    except Exception as exc:
        failed += 1
        print("FAIL", name, repr(exc))
        if hasattr(sys, "print_exception"):
            sys.print_exception(exc)
print("{} passed, {} failed".format(len(names) - failed, failed))
sys.exit(1 if failed else 0)
