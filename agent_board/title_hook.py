"""Хук UserPromptSubmit для розмов дошки: Claude Code сам перейменовує розмову за темою картки.
Дошці передається лише id розмови, без тексту. Будь-яка помилка — мовчки нічого не робимо."""
import http.client
import json
import sys


def main() -> None:
    try:
        port = int(sys.argv[1])
        sid = json.load(sys.stdin)["session_id"]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
        conn.request("POST", "/api/title", json.dumps({"id": sid}),
                     {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
        title = json.loads(conn.getresponse().read()).get("title")
        if isinstance(title, str) and title:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title}}))
    except Exception:
        pass


if __name__ == "__main__":
    main()
