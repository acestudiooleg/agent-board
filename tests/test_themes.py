import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_md import HTML

WEB = Path(__file__).resolve().parent.parent / "web"
JS = (WEB / "themes.js").read_text(encoding="utf-8")
CSS = (WEB / "themes.css").read_text(encoding="utf-8")
ENV = """
const store = {"agent-board-theme": STORED}, classes = new Set(), props = {};
const window = {}, localStorage = {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }};
const document = {querySelector: () => null, dispatchEvent() {}, documentElement: {
  classList: {toggle: (c, on) => on ? classes.add(c) : classes.delete(c)}, style: {setProperty: (k, v) => { props[k] = v; }, removeProperty: k => { delete props[k]; }}}};
class Event {}
"""


@unittest.skipUnless(shutil.which("node"), "потрібен node")
class ThemesTest(unittest.TestCase):
    def run_js(self, code, stored=None):
        src = ENV.replace("STORED", json.dumps(stored)) + JS + "\nconst T = window.Themes;\nconsole.log(JSON.stringify((() => {" + code + "})()));"
        r = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_palettes_are_complete(self):
        themes = self.run_js("return T.list")
        self.assertEqual(len({t["id"] for t in themes}), len(themes))
        self.assertGreaterEqual(sum(not t["light"] for t in themes), 8)
        self.assertGreaterEqual(sum(t["light"] for t in themes), 2)
        for t in themes:
            self.assertEqual(len(t["ansi"]), 16, t["id"])
            for c in [t[k] for k in ("bg", "bg2", "line", "fg", "muted", "accent", "green", "warn", "violet", "red")] + t["ansi"]:
                self.assertRegex(c, r"^#[0-9a-f]{6}$", t["id"])

    def test_text_is_readable_in_every_theme(self):
        bad = self.run_js("""const bad = [];
          for (const t of T.list) { const v = T.vars(t);
            for (const k of ["fg", "muted", "accent", "green", "warn", "violet", "red", "strong"]) for (const bg of [t.bg, t.bg2])
              if (T.contrast(v[k], bg) < 4.5) bad.push([t.id, k, v[k], bg]);
            if (T.contrast(v["on-accent"], v.accent) < 4.5) bad.push([t.id, "on-accent"]);
            if (T.contrast(T.termTheme(t).foreground, t.bg) < 4.5) bad.push([t.id, "term"]); }
          return bad;""")
        self.assertEqual(bad, [])

    def test_choice_is_restored_and_space_themes_clean_up(self):
        state = "return [T.id, [...classes].sort(), Object.keys(props).length > 0, T.term().background]"
        self.assertEqual(self.run_js(state, "nord"), ["nord", ["flat"], True, "#2e3440"])
        self.assertEqual(self.run_js(state, "latte"), ["latte", ["flat", "light"], True, "#eff1f5"])
        self.assertEqual(self.run_js(state, "deep"), ["deep", ["theme-deep"], False, "#0b1524"])
        for old in (None, "glare", "такої-немає"):
            self.assertEqual(self.run_js(state, old), ["glare", [], False, "#0b1524"])
        self.assertEqual(self.run_js("T.set('latte'); T.set('deep'); " + state), ["deep", ["theme-deep"], False, "#0b1524"])
        self.assertEqual(self.run_js("T.set('nord'); return store['agent-board-theme']"), "nord")

    def test_terminal_palette(self):
        got = self.run_js("return Object.keys(T.termTheme(T.list[0])).sort()")
        self.assertIn("brightMagenta", got)
        self.assertEqual(len(got), 21)
        self.assertEqual(self.run_js("T.set('githublight'); const a = T.termContrast(); T.set('nord'); return [a, T.termContrast()]"), [4.5, 1])

    def test_css_uses_only_defined_variables(self):
        defined = {"--t-" + k for k in self.run_js("return Object.keys(T.vars(T.list[0]))")}
        self.assertEqual(set(re.findall(r"--t-[a-z0-9-]+", CSS)) - defined, set())
        self.assertIn('href="/themes.css"', HTML)
        self.assertLess(HTML.index("</style>"), HTML.index('href="/themes.css"'))  # після основних стилів: однакова вага правил


if __name__ == "__main__":
    unittest.main()
