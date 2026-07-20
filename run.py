#!/usr/bin/env python3
"""
Launcher script for ByteBack.

Usage:
    python3 run.py
    sudo python3 run.py   # recommended for raw disk / unallocated scanning
"""

from main import main

if __name__ == "__main__":
    raise SystemExit(main())
