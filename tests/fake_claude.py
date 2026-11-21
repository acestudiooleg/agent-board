import json
import sys
import time

prompt = sys.stdin.read()
if "FAIL" in prompt:
    sys.stderr.write("boom")
    sys.exit(1)
if "GARBAGE" in prompt:
    print(json.dumps({"result": "не знаю", "is_error": False}))
    sys.exit(0)
if "HANG" in prompt:
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open("child.pid", "w") as fh:  # cwd — тека, яку передав run_claude
        fh.write(str(child.pid))
    time.sleep(60)
if "ENVCHECK" in prompt:
    import os
    about = f"env={os.environ.get('CLAUDE_CONFIG_DIR', 'none')} cwd={os.getcwd()}"
    print(json.dumps({"result": json.dumps({"about": about}), "is_error": False}))
    sys.exit(0)
if "SLOW" in prompt:
    time.sleep(1)
answer = {
    "about": "Міграція схеми", "done": ["тести", "схема"], "waiting_for": "Видаляти таблицю?",
    "related_projects": ["templates"], "likely_project": "tutor-app",
}
print(json.dumps({"result": "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```", "is_error": False}))
