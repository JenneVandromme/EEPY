"""Check the packages we need are installed, and say so kindly if not.

Without this, running the validator outside the project's virtualenv ends in a
raw traceback -- "ModuleNotFoundError: No module named 'pydantic'" -- which is
the exact kind of message this tool exists to avoid. The commonest mistake is
simply forgetting to activate .venv-robot, so that is what we suggest first.

Import this FIRST, before any third-party import, so the check runs before
Python has a chance to fail on its own.
"""

from __future__ import annotations

import importlib.util
import sys

# import name -> what to install it as
REQUIRED = {
    "yaml": "pyyaml",
    "pydantic": "pydantic",
    "numpy": "numpy",
    "scipy": "scipy",
}

ACTIVATE_HINT = """
It looks like the project's environment is not active. From the EEPY folder:

    source .venv-robot/bin/activate          (macOS / Linux)
    .venv-robot\\Scripts\\Activate.ps1         (Windows PowerShell)

then run the command again. If you have never set that environment up, see
"1. Setting everything up on your computer" in the README.
""".rstrip()


def require() -> None:
    """Exit with a readable message if anything we need is missing."""
    missing = [
        package
        for module, package in REQUIRED.items()
        if importlib.util.find_spec(module) is None
    ]
    if not missing:
        return

    names = ", ".join(missing)
    print(
        f"Cannot run: {names} {'is' if len(missing) == 1 else 'are'} not installed.",
        file=sys.stderr,
    )
    print(ACTIVATE_HINT, file=sys.stderr)
    print(
        "\nIf the environment IS active, install what is missing with:\n"
        "    pip install -r requirements-robot.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)
