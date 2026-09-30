import http.server
import socket
import threading

import pytest

from conftest import wait_task
from mrx.__main__ import check_port


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_check_port_detects_free_old_mrx_and_other_programs():
    assert check_port(free_port()) is None

    class Old(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"<title>M.R.X. - Command Center</title>")
        def log_message(self, *a): ...

    class Other(Old):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"hello from something else")

    for handler, expected in ((Old, "mrx"), (Other, "other")):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        assert check_port(srv.server_port) == expected
        srv.shutdown()


async def test_greeting_variants_all_answered_locally(rt):
    for g in ("hello Mr X", "hello mr x", "Hey Mister X", "hi m r x", "hello mrx", "hey there", "Hello, Mr.X!", "hey mrx how are you", "good morning mrx", "ok hey mrx"):
        r = await rt.agent.submit(g, "t")
        t = await wait_task(rt, r["task_id"])
        assert t.result.startswith("Hello! I'm M.R.X."), (g, t.result)


async def test_real_commands_are_not_mistaken_for_greetings(rt):
    from mrx.agent import planner
    from mrx.agent.context import Context
    for cmd in ("open chrome", "hey open notepad", "hello world program", "yo"):
        it = planner.plan_clause(cmd, Context())
        if cmd == "yo":
            assert it and it.label == "greeting"
        else:
            assert it is None or it.label != "greeting", cmd
