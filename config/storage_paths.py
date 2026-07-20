"""
Persistent storage paths for application state.
"""

import os

STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "byteback")
LOCK_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "byteback")
STATE_FILENAME = "scan_state.json"
IMAGE_DIR = os.path.join(STATE_DIR, "images")
IMAGE_REGISTRY_FILENAME = "image_registry.json"
PREVIEW_DIR_NAME = "byteback_previews"
INSTANCE_LOCK_FILENAME = "instance.lock"
CONTROL_SOCKET_FILENAME = "control.sock"
