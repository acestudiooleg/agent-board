import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "web/index.html").read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("node"), "потрібен node")
class MarkdownLinksTest(unittest.TestCase):
    def md(self, text: str) -> str:
        src = re.search(r"const esc = .*?;\n", HTML).group(0) + re.search(r"// Маленький безпечний Markdown.*?(?=function setHtml)", HTML, re.S).group(0)
        out = subprocess.run(["node", "-e", src + "process.stdout.write(md(JSON.parse(process.argv[1])))", json.dumps(text)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def hrefs(self, text: str) -> list[str]:
        return re.findall(r'<a href="([^"]*)"', self.md(text))

    def test_bare_url_without_trailing_punctuation(self):
        self.assertEqual(self.hrefs("Див. https://example.com/a_b_c?x=1&y=2#z. Далі (https://example.com/q)."),
                         ["https://example.com/a_b_c?x=1&amp;y=2#z", "https://example.com/q"])

    def test_balanced_parens_stay(self):
        self.assertEqual(self.hrefs("https://uk.wikipedia.org/wiki/A_(b)"), ["https://uk.wikipedia.org/wiki/A_(b)"])

    def test_local_address_gets_scheme(self):
        self.assertEqual(self.hrefs("відкрий 127.0.0.1:8090/plans/x.html і localhost:8091"),
                         ["http://127.0.0.1:8090/plans/x.html", "http://localhost:8091"])

    def test_inside_code_bold_quote_table_and_pre(self):
        for text in ("`https://example.com/x`", "**https://example.com/x**", '"https://example.com/x"', "<https://example.com/x>",
                     "| a |\n|---|\n| https://example.com/x |", "```\nhttps://example.com/x\n```", "- https://example.com/x"):
            self.assertEqual(self.hrefs(text), ["https://example.com/x"], text)

    def test_markdown_link_is_not_linked_twice(self):
        html = self.md("[https://example.com/*a*](https://example.com/*a*) і [**жирний** `код`](https://example.com/y)")
        self.assertEqual(re.findall(r'<a href="([^"]*)"', html), ["https://example.com/*a*", "https://example.com/y"])
        self.assertEqual(html.count("<a "), html.count("</a>"))
        self.assertIn("<strong>жирний</strong>", html)

    def test_unsafe_stays_text(self):
        html = self.md('[x](javascript:alert(1)) https://e.com/"onmouseover="x <img src=x onerror=1>')
        self.assertEqual(re.findall(r'<a href="([^"]*)"', html), ["https://e.com/"])
        self.assertNotIn("<img", html)
        self.assertNotIn('onmouseover="', html)


if __name__ == "__main__":
    unittest.main()
