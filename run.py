#!/usr/bin/env python3
"""
Launcher script for ByteBack.

Usage:
    python3 run.py

A pkexec helper is started once at launch for privileged disk access.
"""

import sys

if "--helper" in sys.argv:
    from services.root_helper import helper_main

    helper_main()
    raise SystemExit(0)

from main import main

if __name__ == "__main__":
    raise SystemExit(main())
