"""Fast checks that need no ffmpeg: every module imports, no undefined names, the Whisper runner compiles."""
import ast
import builtins
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import stub_tk2  # noqa: E402,F401  (GUI modules import tkinter)


def _modules():
    for path in sorted((ROOT / "src" / "condense").rglob("*.py")):
        yield path, ".".join(path.relative_to(ROOT / "src").with_suffix("").parts).replace(".__init__", "")


def test_no_undefined_names():
    problems = {}
    for path, mod in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = set(dir(builtins)) | {"__file__", "__name__"} | set(dir(importlib.import_module(mod)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                defined.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
        missing = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined})
        if missing:
            problems[mod] = missing
    assert not problems, problems


def test_whisper_runner_compiles():
    from condense.asr.runner import WHISPER_RUNNER

    compile(WHISPER_RUNNER, "WHISPER_RUNNER", "exec")


def test_version_and_cli():
    import condense
    from condense.__main__ import main

    assert condense.__version__
    assert main(["--version"]) == 0
