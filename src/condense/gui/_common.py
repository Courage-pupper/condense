"""Names shared by the GUI modules (stdlib, Tk, core, platform, theme, widgets)."""
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
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from condense import __version__
from condense.asr.runner import WHISPER_RUNNER
from condense.core.exports import RTFConfidenceWriter
from condense.core.transcript import TranscriptDocument
from condense.core.util import *  # noqa: F401,F403
from condense.gui.theme import *  # noqa: F401,F403
from condense.gui.widgets import IS_MAC, MOD, FlowFrame, ScrollFrame
from condense.pipeline.worker import PipelineWorker
from condense.platform import APP_SUPPORT_DIR, KeychainSecret, MacAppInstaller, SystemProbe
from condense.settings import SettingsStore
