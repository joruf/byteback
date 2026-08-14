#!/usr/bin/env python3
"""
Launcher script for ByteBack.

Usage:
    python3 run.py

A pkexec helper is started once at launch for privileged disk access.
"""

import sys

# --- what this application needs ----------------------------------------------------------------
# Checked before anything below is imported. Whatever is missing is installed in a window that
# shows the work as it happens; see bootstrap_ui.py. `--setup` opens that window even when nothing
# is missing, which is how to see what is installed.
from bootstrap_ui import Need, ensure  # noqa: E402

NEEDS = (
    Need(label="Tk toolkit", module="tkinter", packages=("python3-tk",)),
    Need(label="PolicyKit helper", command="pkexec", packages=("policykit-1",)),
    Need(label="MIME detection", module="magic", packages=("python3-magic",), optional=True,
         note="file types are guessed from the name instead"),
    Need(label="Desktop opener", command="xdg-open", packages=("xdg-utils",), optional=True,
         note="files do not open in their application"),
)

# Only when the application is actually being started. Importing this module — which the test
# suite does — should not check anything, let alone put an installer window on screen.
if __name__ == "__main__":
    # Taken out of the arguments once it has been read, so the application's own parser does
    # not trip over a flag that was never meant for it.
    _SETUP = "--setup" in sys.argv

    if _SETUP:
        sys.argv.remove("--setup")

    if not ensure("ByteBack", NEEDS, force=_SETUP):
        raise SystemExit(1)


if "--helper" in sys.argv:
    from services.root_helper import helper_main

    helper_main()
    raise SystemExit(0)

from main import main

if __name__ == "__main__":
    raise SystemExit(main())
