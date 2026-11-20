"""Persisted GUI settings."""
import json
import math
import os
import platform
import plistlib
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from condense.platform import APP_SUPPORT_DIR, LEGACY_APP_SUPPORT_DIR


class SettingsStore:
    """GUI settings persisted between launches (never the Hugging Face token)."""

    PATH = APP_SUPPORT_DIR / "settings.json"

    @classmethod
    def _migrate(cls) -> None:
        """Carry settings and the task queue over from the app's previous name (Silicon Video Pipeline)."""
        if APP_SUPPORT_DIR.exists() or not LEGACY_APP_SUPPORT_DIR.exists():
            return
        try:
            shutil.copytree(LEGACY_APP_SUPPORT_DIR, APP_SUPPORT_DIR)
        except Exception:
            pass

    @classmethod
    def load(cls) -> Dict:
        cls._migrate()
        try:
            return json.loads(cls.PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @classmethod
    def save(cls, data: Dict) -> None:
        try:
            cls.PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = cls.PATH.with_name(cls.PATH.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cls.PATH)
        except Exception:
            pass
