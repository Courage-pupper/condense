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

from condense import __version__, updates
from condense.asr.runner import WHISPER_RUNNER, is_harmless_output, runner_command
from condense.core.exports import RTFConfidenceWriter
from condense.core.transcript import TranscriptDocument
from condense.core.util import *  # noqa: F401,F403
from condense.gui.theme import *  # noqa: F401,F403
from condense.gui.widgets import (EDIT_MOD, EDIT_MOD_LABEL, IS_MAC, MOD, FlowFrame, ScrollFrame, geometry_fits,
                                  is_shortcut, kbd, screen_rects)
from condense.pipeline.worker import PipelineWorker
from condense.platform import (APP_SUPPORT_DIR, DEFAULT_BACKEND, DEFAULT_ENCODER, GPU_DIARIZATION, HW_QUALITY_NAME, IS_MAC_PLATFORM,
                               PLATFORM, VIDEO_ENCODERS, WHISPER_BACKENDS, AppInstaller, KeychainSecret, MacAppInstaller,
                               SecretStore, SystemProbe, default_output_dir, log_file, open_command)
from condense.settings import SettingsStore
