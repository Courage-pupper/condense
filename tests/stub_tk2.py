"""Fake tkinter for headless tests: permissive widgets + a Text widget that really stores its content."""
import sys, types


class W:
    _width = 1000

    def __init__(self, *a, **k):
        self._kw = dict(k)
        self._val = k.get("value")
        self._binds = {}

    def __getattr__(self, name):
        ints = {"winfo_width": self._width, "winfo_height": 700, "winfo_reqwidth": 120, "winfo_reqheight": 28,
                "winfo_screenwidth": 1512, "winfo_screenheight": 982}
        if name in ints:
            return lambda *a, **k: ints[name]
        consts = {"selection": (), "get_children": (), "geometry": "1200x800+40+40", "sash_coord": (480, 0),
                  "bbox": (0, 0, 1, 1), "identify_row": "", "winfo_pointerx": 10, "winfo_pointery": 10, "yview": (0.0, 1.0), "exists": True, "winfo_containing": None, "poll": 0}
        if name in consts:
            return lambda *a, **k: consts[name]
        return lambda *a, **k: W()

    def cget(self, key):
        return self._kw.get(key, 0)

    def configure(self, *a, **k):
        self._kw.update(k)

    config = configure

    def bind(self, seq, fn=None, add=None):
        self._binds[seq] = fn

    def after(self, ms, fn=None, *a):
        return None

    def after_idle(self, fn, *a):
        fn(*a)

    def get(self):
        return self._val

    def set(self, v):
        self._val = v

    def __str__(self):
        return f".!{type(self).__name__.lower()}{id(self)}"


class Text(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.content = ""
        self.tags = {}

    def _pos(self, index):
        if index in ("insert", "insert-1c"):
            l, c = self._pos("end-1c")
            return (l, max(0, c - 1)) if index == "insert-1c" else (l, c)
        if index in ("end", "end-1c"):
            lines = self.content.split("\n")
            return len(lines), len(lines[-1])
        line, col = index.split(".")
        return int(line), int(col)

    def _offset(self, index):
        line, col = self._pos(index)
        lines = self.content.split("\n")
        return sum(len(l) + 1 for l in lines[: line - 1]) + col

    def insert(self, index, chars, *tags):
        self.content += chars

    def mark_set(self, name, index):
        self.marks = getattr(self, "marks", {}); self.marks[name] = index

    def delete(self, a, b=None):
        if a == "1.0" and b in ("end", None):
            self.content = ""
        else:
            self.content = self.content[: self._offset(a)] + self.content[self._offset(b):]

    def get(self, a, b):
        return self.content[self._offset(a): self._offset(b)]

    def index(self, idx):
        l, c = self._pos(idx)
        return f"{l}.{c}"

    def tag_add(self, tag, a, b):
        self.tags[tag] = (a, b)

    def tag_remove(self, tag, *a):
        self.tags.pop(tag, None)


tk = types.ModuleType("tkinter")
for n in ["Tk", "Frame", "Label", "Entry", "Scale", "Spinbox", "Checkbutton", "Toplevel", "Canvas", "PanedWindow",
          "StringVar", "BooleanVar", "IntVar", "DoubleVar"]:
    setattr(tk, n, type(n, (W,), {}))
tk.Text = Text


class Listbox(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.items = []; self.sel = ()
    def delete(self, a, b=None): self.items = []; self.sel = ()
    def insert(self, idx, item): self.items.append(item)
    def curselection(self): return self.sel
    def selection_set(self, i): self.sel = (i,)


tk.Listbox = Listbox


class PhotoImage:
    def __init__(self, data=None, format=None, **k):
        assert data[:2] == b"P6"
        self.data = data
        hdr = data.split(b"\n", 3)
        self.w, self.h = (int(v) for v in hdr[1].split())


tk.PhotoImage = PhotoImage
sd = types.ModuleType("tkinter.simpledialog")
sd.askstring = lambda *a, **k: sd._next
sd._next = None
tk.Menu = type("Menu", (W,), {})


class Font:
    def __init__(self, root=None, family="", size=10, weight="normal", **k):
        self.size = size
    def configure(self, **k):
        self.size = k.get("size", self.size)
    def metrics(self, key):
        return int(self.size * 1.3)
    def __str__(self):
        return f"font{id(self)}"


font_mod = types.ModuleType("tkinter.font")
font_mod.Font = Font
for n in ["X", "Y", "BOTH", "LEFT", "RIGHT", "TOP", "BOTTOM", "WORD", "FLAT", "RAISED", "CENTER", "VERTICAL", "HORIZONTAL", "DISABLED", "NORMAL"]:
    setattr(tk, n, n.lower())
tk.END = "end"
ttk = types.ModuleType("tkinter.ttk")
for n in ["Style", "Button", "Combobox", "Treeview", "Scrollbar", "Notebook", "Progressbar", "Checkbutton"]:
    setattr(ttk, n, type(n, (W,), {}))
fd = types.ModuleType("tkinter.filedialog")
fd.askopenfilename = lambda **k: fd._next
fd.askopenfilenames = lambda **k: fd._next_many
fd._next_many = ()
fd.askdirectory = lambda **k: ""
fd._next = ""
mb = types.ModuleType("tkinter.messagebox")
mb.askyesno = lambda *a, **k: True
mb.showerror = lambda *a, **k: print("SHOWERROR:", a)
mb.showinfo = lambda *a, **k: None
tk.ttk, tk.filedialog, tk.messagebox, tk.font, tk.simpledialog = ttk, fd, mb, font_mod, sd
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": fd, "tkinter.messagebox": mb, "tkinter.font": font_mod, "tkinter.simpledialog": sd})
