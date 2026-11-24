import re
import shutil
import subprocess
import unittest

from tests.test_md import HTML

JS = """
let reloads = 0, focus = "BODY", idle = true;
const S = {build: null}, window = {Term: {idle: () => idle}}, Term = window.Term;
const location = {reload() { reloads++; }}, render = () => {}, loadProjects = () => {};
const document = {get activeElement() { return {tagName: focus, matches: () => false}; }, documentElement: {classList: {toggle() {}}}, body: {classList: {toggle() {}}}};
%s
const out = [], step = () => { out.push(reloads); reloads = 0; };
apply({build: "a"}); step();                       // перше завантаження
apply({build: "a"}); step();                       // та сама версія
focus = "TEXTAREA"; apply({build: "b"}); step();   // нова, але ви друкуєте
focus = "BODY"; idle = false; maybeReload(); step(); // є ненадісланий текст або запис
idle = true; maybeReload(); step();                // вільно — оновлюємо
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "потрібен node")
class ReloadTest(unittest.TestCase):
    def test_reload_only_when_nothing_is_lost(self):
        src = re.search(r"function maybeReload\(\).*?(?=fetch\(\"/api/board\"\))", HTML, re.S).group(0)
        r = subprocess.run(["node", "-e", JS % src], capture_output=True, text=True, timeout=20)
        self.assertEqual((r.stdout.strip(), r.stderr), ("[0,0,0,0,1]", ""))


if __name__ == "__main__":
    unittest.main()
