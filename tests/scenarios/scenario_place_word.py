"""_place_word scrolls by display lines. A word cut off at the window's bottom edge has a box only a pixel or two
high; stepping by that height once sent the view to the end of the transcript."""
import os
import sys
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2  # noqa: F401,E402
from condense.gui.editor import EditorMixin  # noqa: E402


class FakeText:
    """Display lines of differing heights (paragraph spacing makes some taller); bbox clips at the window's edge."""

    def __init__(self, heights, window_h):
        self.heights, self.window_h, self.top = heights, window_h, 0

    def _y(self, line):
        return sum(self.heights[self.top:line]) if line >= self.top else None

    def yview(self, index):
        self.top = int(index.split(".")[0])

    def see(self, index):
        self.yview(index)

    def yview_scroll(self, n, what):
        self.top = max(0, min(len(self.heights) - 1, self.top + n))

    def bbox(self, index):
        line = int(index.split(".")[0])
        y = self._y(line)
        if y is None or y >= self.window_h:
            return None
        return (10, y, 16, min(self.heights[line], self.window_h - y))  # cut off at the bottom edge


class FakeFont:
    def metrics(self, what):
        return 20


class App(EditorMixin):
    def __init__(self, pt):
        self.preview_text = pt
        self.f = {"transcript": FakeFont()}
        self.word_spans = [{"line": i, "c0": 0, "start": float(i)} for i in range(len(pt.heights))]

    def _span_near(self, t):
        return int(t)


def test_a_cut_off_word_does_not_send_the_view_to_the_end():
    heights = [20, 20, 40] * 20  # every third line a taller paragraph break: one step lands the word at the edge
    pt = FakeText(heights, 270)
    app = App(pt)
    got = app._place_word(30.0, 190)
    assert got is not None, "the word left the screen"
    assert abs(got - 190) <= 30, got
    assert pt.top < 30  # scrolled up to place it lower, not down to the end


def test_a_step_past_the_screen_is_taken_back():
    pt = FakeText([20, 30, 50] * 20, 270)  # ten lines up are taller than the window
    app = App(pt)
    got = app._place_word(30.0, 190)
    assert got is not None and abs(got - 190) <= 50, got


def test_place_word_lands_where_asked_on_even_lines():
    pt = FakeText([20] * 60, 270)
    app = App(pt)
    assert app._place_word(30.0, 100) == 100


if __name__ == "__main__":
    test_a_cut_off_word_does_not_send_the_view_to_the_end()
    print("  ✓ a word cut off at the bottom edge is placed, the view does not run to the end")
    test_a_step_past_the_screen_is_taken_back()
    print("  ✓ a step that takes the word off the screen is taken back and halved")
    test_place_word_lands_where_asked_on_even_lines()
    print("  ✓ on even lines the word lands where asked")
    print("PLACE WORD PASSED")
