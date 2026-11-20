import os
import importlib.util, json, os, queue, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_basic")
from _compat import pipe
ok = lambda m: print("  ✓", m)
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
for d in ("Interview O'Brien", "P04_trimmed", "Broken", "P02", "Session_merged", "Group 1 merged"):
    shutil.rmtree(media / d, ignore_errors=True)
base_settings = {"respect_llc": True, "hb_container": "MKV", "audio_format": "WAV (16kHz Mono - Optimal for Whisper)", "whisper_backend": "MLX (Apple Silicon GPU)",
                 "diarize_mode": "Disabled (No Speaker Labels)", "num_speakers": 0, "fmt_srt": True, "fmt_vtt": False, "fmt_txt": False, "fmt_rtf": False,
                 "fmt_para_rtf": True, "fmt_para_txt": True}

print("[1] worker: mixed task queue")
tasks = [
    {"id": "task_0001", "name": "Group 1 merged", "inputs": [str(media / "Interview O'Brien.mp4"), str(media / "P02.mp4")],
     "steps": {"compress": False, "audio": True, "whisper": True}, "output_dir": str(media / "Group 1 merged"),
     "settings": dict(base_settings, whisper_model="small", whisper_lang="en (English)")},
    {"id": "task_0002", "name": "Broken", "inputs": [str(media / "Broken.mp4")], "steps": {"whisper": True}, "output_dir": str(media / "Broken"),
     "settings": dict(base_settings, whisper_model="large-v3-turbo", whisper_lang="de (German)")},
    {"id": "task_0003", "name": "P04_trimmed", "inputs": [str(media / "P04_trimmed.mp4")], "steps": {"compress": False, "audio": False, "whisper": True},
     "output_dir": str(media / "P04_trimmed"), "settings": dict(base_settings, whisper_model="large-v3-turbo", whisper_lang="de (German)")},
    {"id": "task_0004", "name": "P02", "inputs": [str(media / "P02.mp4")], "steps": {"audio": True}, "output_dir": str(media / "P02"),
     "settings": dict(base_settings, audio_format="M4A")},
]
q = queue.Queue()
pipe.PipelineWorker({"tasks": tasks, "hf_token": ""}, q, threading.Event()).run()
msgs = [q.get() for _ in range(q.qsize())]
final = {}
for m in msgs:
    if m["type"] == "job_update" and "status" in m:
        final[m["job_id"]] = m["status"]
print("   ", final)
assert final == {"task_0001": "Completed", "task_0002": "Failed", "task_0003": "Completed", "task_0004": "Completed"}
g = media / "Group 1 merged"
print("    merge task folder:", sorted(p.name for p in g.iterdir()))
d1 = pipe.TranscriptDocument.load(g / "Group 1 merged.transcript.json")
assert d1.data["provenance"]["whisper"]["model"] == "small" and d1.data["provenance"]["whisper"]["language_setting"] == "en"
assert [Path(i["path"]).name for i in d1.data["provenance"]["inputs"]] == ["Interview O'Brien.mp4", "P02.mp4"]
d3 = pipe.TranscriptDocument.load(media / "P04_trimmed" / "P04_trimmed.transcript.json")
assert d3.data["provenance"]["whisper"]["model"] == "large-v3-turbo" and d3.data["provenance"]["whisper"]["language_setting"] == "de"
assert sorted(p.name for p in (media / "P02").iterdir()) == ["P02_audio.m4a"]
ok("merge task, single tasks with their own settings, audio-only task; broken one isolated")

print("[2] GUI: task queue")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app.var_stage_compress.set(False); app.var_stage_audio.set(False); app.var_stage_whisper.set(True)
app.var_whisper_model.set("medium")
app._enqueue_video_paths([str(media / "P02.mp4"), str(media / "P03_48k.mp4"), str(media / "Interview O'Brien.mp4")])
assert [t["name"] for t in app.jobs] == ["P02", "P03_48k", "Interview O'Brien"] and app.jobs[0]["settings"]["whisper_model"] == "medium"
print("    row:", app._job_values(app.jobs[0]))
ok("+ Videos: one task per video with default steps and a settings snapshot")
app.var_whisper_model.set("large-v3")
assert app.jobs[0]["settings"]["whisper_model"] == "medium"
ok("changing Settings later doesn't silently change queued tasks")

stub_tk2.fd._next_many = (str(media / "P03_48k.mp4"), str(media / "P02.mp4"))
app._add_merge_task()
m = app.jobs[-1]
assert [Path(p).name for p in m["inputs"]] == ["P02.mp4", "P03_48k.mp4"] and m["name"] == "P0_merged" or m["name"].endswith("_merged")
print("    merge task:", m["name"], "→", m["output_dir"], "|", app._job_values(m)[1])
print("    details:", app.lbl_task_title.cget("text"), "|", app.lbl_task_settings.cget("text"))
app.queue_tree.selection = lambda: (m["id"],)
app.var_task_name.set("Workshop day 1"); app._on_task_name()
assert m["name"] == "Workshop day 1" and m["output_dir"].endswith("Workshop day 1")
app.lst_inputs.selection_set(1); app._task_input_move(-1)
assert [Path(p).name for p in m["inputs"]] == ["P03_48k.mp4", "P02.mp4"]
app.var_task_audio.set(True); app._on_task_steps()
assert m["steps"] == {"compress": False, "audio": True, "whisper": True}
app._task_use_current_settings()
assert m["settings"]["whisper_model"] == "large-v3"
ok("merge task: rename (output follows), reorder videos, change steps, apply current settings")

ids = [app.jobs[0]["id"], app.jobs[1]["id"]]
app.queue_tree.selection = lambda: tuple(ids)
app._combine_selected()
c = app.jobs[0]
assert len(app.jobs) == 3 and [Path(p).name for p in c["inputs"]] == ["P02.mp4", "P03_48k.mp4"] and c["settings"]["whisper_model"] == "medium"
ok("combine two single tasks into one merge task (keeps their settings)")
app.queue_tree.selection = lambda: (c["id"],)
app._split_merge_task()
assert [t["name"] for t in app.jobs[:2]] == ["P02", "P03_48k"] and len(app.jobs) == 4
ok("split a merge task back into single tasks")

captured = {}
class FakeWorker:
    def __init__(self, config, q, ev): captured.update(config)
    def start(self): pass
    def is_alive(self): return False
pipe.PipelineWorker = FakeWorker
app._start_pipeline()
assert len(captured["tasks"]) == 4 and captured["tasks"][-1]["name"] == "Workshop day 1"
ok("Run sends the tasks (each with its own steps and settings) to the worker")

app.jobs[0]["status"] = "Running"; app.jobs[1]["status"] = "Completed"; app.jobs[1]["doc_path"] = str(media / "P04_trimmed" / "P04_trimmed.transcript.json")
app._save_queue()
holder.clear(); pipe.launch_gui(); app2 = holder["app"]
assert [t["status"] for t in app2.jobs] == ["Pending", "Completed", "Pending", "Pending"] and app2.jobs[0]["stage"].startswith("Interrupted")
ok("queue restored after restart; an interrupted task goes back to Pending")

app2.queue_tree.selection = lambda: (app2.jobs[1]["id"],)
app2._on_job_selected()
assert app2.active_doc is not None and app2.active_doc.base_name == "P04_trimmed"
before = app2.active_doc.path
app2.msg_queue.put({"type": "transcript_ready", "job_ids": [app2.jobs[2]["id"]], "doc_path": str(g / "Group 1 merged.transcript.json")})
app2._poll_queue()
assert app2.active_doc.path == before and app2.jobs[2]["doc_path"]
ok("a newly finished transcript no longer replaces the one you're viewing")
print("\nALL TESTS PASSED")
