"""Test helper: one namespace over all Condense modules.

The scenario scripts were written against the original single-file app (`pipe.TranscriptDocument`,
`pipe.subprocess.Popen = ...`, `pipe.PipelineWorker = Fake`). This object resolves a name in whichever module
defines it, and patches every module that holds that name, so the scenarios run unchanged against the package.
"""
import importlib

MODULES = [
    "condense.core.util", "condense.core.exports", "condense.core.transcript", "condense.media.losslesscut",
    "condense.asr.runner", "condense.pipeline.encode", "condense.pipeline.transcribe", "condense.pipeline.worker", "condense.platform.mac", "condense.settings",
    "condense.gui.theme", "condense.gui.widgets", "condense.gui._common", "condense.gui.queue_panel",
    "condense.gui.settings_tab", "condense.gui.editor", "condense.gui.navigation", "condense.gui.history", "condense.gui.review_marks", "condense.gui.player", "condense.gui.rerun",
    "condense.gui.video", "condense.gui.updates_ui", "condense.gui.gpu_check", "condense.gui.file_dialogs", "condense.gui.cutting", "condense.gui.trim_dialog", "condense.gui.find", "condense.gui.project", "condense.gui.app", "condense.core.project", "condense.core.cuts", "condense.media.trim", "condense.updates",
]


class _Namespace:
    def __init__(self):
        object.__setattr__(self, "_mods", [importlib.import_module(m) for m in MODULES])

    def __getattr__(self, name):
        for m in object.__getattribute__(self, "_mods"):
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        hit = False
        for m in object.__getattribute__(self, "_mods"):
            if name in vars(m):
                setattr(m, name, value)
                hit = True
        if not hit:
            raise AttributeError(f"no Condense module defines {name!r}")


pipe = _Namespace()
