"""Comments (codings without a code) and the commentary export: blocks of a few minutes, every block a row, each item in
the block where it starts, comments + coding memos + segment notes, .xlsx and .csv, text that looks like a formula."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from condense.core import coding as cod  # noqa: E402
from condense.core import commentary as cm  # noqa: E402
from test_word_ids import make  # noqa: E402


def setup(tmp_path):
    doc = make(tmp_path, n_segs=60)
    cb = cod.Codebook.for_project(tmp_path)
    making = cb.add("Making")
    w = doc.segments[2]["words"]
    k1 = cod.add_coding(doc, cod.COMMENT, w[0]["start"], w[-1]["end"], w[0]["wid"], w[-1]["wid"],
                        memo="They start building.\nSam takes the sticks.")
    k2 = cod.add_coding(doc, cod.COMMENT, w[0]["start"], w[-1]["end"], w[0]["wid"], w[-1]["wid"], memo="A second look")
    k3 = cod.add_coding(doc, cod.COMMENT, 70.0, 110.0, memo="=everyone at the table")  # by time, looks like a formula
    w9 = doc.segments[9]["words"]
    cod.add_coding(doc, making, w9[0]["start"], w9[-1]["end"], w9[0]["wid"], w9[-1]["wid"], memo="the raft")
    cod.add_coding(doc, making, w9[0]["start"], w9[-1]["end"] + 1, memo="")  # no memo: not commentary
    doc.segments[20]["note"] = "- check this word"
    doc.save()
    return doc, cb, (k1, k2, k3)


def test_comments_are_codings_without_a_code(tmp_path):
    doc, cb, (k1, k2, k3) = setup(tmp_path)
    assert k1 != k2  # two comments on one stretch stay two
    assert cod.get_coding(doc, k1)["memo"] == "They start building.\nSam takes the sticks."  # paragraphs kept
    assert cb.get(cod.COMMENT)["name"] == "✎ Comment" and cod.COMMENT not in [c["id"] for c in cb.codes]
    assert cb.path_name(cod.COMMENT) == "✎ Comment"
    assert cod.get_coding(doc, k3)["kind"] == "time"
    assert cod.clean_memo("  a   b \r\n\r\n\r\n\r\n c ") == "a b\n\nc"


def test_blocks_and_rows(tmp_path):
    doc, cb, _ = setup(tmp_path)
    dur = cm.duration_of(doc)
    assert dur > 150
    bl = cm.blocks(dur, 1)
    assert bl[0] == (0.0, 60.0) and bl[-1][1] >= dur and len(bl) == int(dur // 60) + 1
    rows = cm.table(doc, 1, cb)
    assert {r["block"] for r in rows} == set(range(len(bl)))          # every block has a row …
    for r in rows:
        if r["kind"]:
            assert r["block_start"] <= r["start"] < r["block_end"] or r["block"] == len(bl) - 1  # … each item where it starts
    kinds = [r["kind"] for r in rows if r["kind"]]
    assert kinds.count("Comment") == 3 and kinds.count("Coding memo") == 1 and kinds.count("Note") == 1
    memo = next(r for r in rows if r["kind"] == "Coding memo")
    assert memo["code"] == "Making" and memo["text"] == "the raft" and memo["passage"]
    by_time = next(r for r in rows if r["text"] == "=everyone at the table")
    assert by_time["passage"] == "" and by_time["start"] == 70.0 and by_time["end"] == 110.0 and by_time["block"] == 1
    assert all(r["first"] for r in rows if not r["kind"])             # an empty block: one row
    only_notes = cm.table(doc, 5, cb, include=("note",))
    assert [r["kind"] for r in only_notes if r["kind"]] == ["Note"]


def test_xlsx_and_csv(tmp_path):
    from openpyxl import load_workbook
    doc, cb, _ = setup(tmp_path)
    sessions = [{"doc": doc, "name": "G1D1", "type": "session", "levels": [("Day", "Day 1"), ("Group", "Group 1")],
                 "attributes": {"condition": "A"}},
                {"path": doc.path, "name": "G1D1 again", "type": "session", "levels": [("Day", "Day 1"), ("Group", "Group 2")],
                 "attributes": {"condition": "B"}}]
    st = cm.export(sessions, tmp_path / "c.xlsx", minutes=1, cb=cb, frames=False, per_sheet=True, attributes=["condition"])
    assert st["sessions"] == 2 and st["frames"] == 0 and st["rows"] == 2 * len(cm.table(doc, 1, cb))
    wb = load_workbook(tmp_path / "c.xlsx")
    assert wb.sheetnames == ["Commentary", "G1D1", "G1D1 again"]
    ws = wb["Commentary"]
    head = [c.value for c in ws[1]]
    assert head == ["Day", "Group", "Type", "Session", "condition"] + cm.COLUMNS
    vals = [[c.value for c in row] for row in ws.iter_rows(min_row=2)]
    assert vals[0][:5] == ["Day 1", "Group 1", "session", "G1D1", "A"] and vals[0][5] == "0:00:00 – 0:01:00"
    assert any(v[-1] == "=everyone at the table" for v in vals)       # text, not a formula
    assert ws.cell(row=1 + next(i for i, v in enumerate(vals, 1) if v[-1] == "=everyone at the table"), column=len(head)).data_type == "s"
    assert any(v[-1] == "They start building.\nSam takes the sticks." for v in vals)
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:")
    st2 = cm.export(sessions[:1], tmp_path / "c.csv", minutes=1, cb=cb)
    with open(tmp_path / "c.csv", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Day", "Group", "Type", "Session"] + [c for c in cm.COLUMNS if c != "Frame"]
    assert any(r[-1] == "'=everyone at the table" for r in rows) and any(r[-1] == "'- check this word" for r in rows)
    assert st2["rows"] == len(rows) - 1
