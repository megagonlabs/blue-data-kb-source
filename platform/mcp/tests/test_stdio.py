"""The stdio transport: every line the server writes to stdout is a JSON-RPC message (#173).

Runs the real server on raw pipes, so no client tolerance hides stray output. LiteLLM's
OpenAI calls go to a local stand-in, so no key or network is needed.
"""
import http.server
import json
import os
import subprocess
import sys
import threading

import pytest

pytest.importorskip("litellm")

_REPLY = {
    "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "return_result", "arguments": '{"result": []}'}}],
    }}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


class _StandIn(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"]))
        body = json.dumps(_REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def llm_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def test_an_llm_tool_call_writes_only_protocol_messages_to_stdout(llm_url, tmp_path):
    env = {
        **os.environ, "BLUE_DATA_KB_SOURCE_DATA_DIR": str(tmp_path), "OPENAI_API_KEY": "sk-test",
        "OPENAI_BASE_URL": llm_url, "OPENAI_API_BASE": llm_url,
    }
    env.pop("BLUE_DATA_LLM_PROVIDER", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "blue_data_kb_mcp.server"], env=env, text=True, bufsize=1,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    def send(message):
        server.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        server.stdin.flush()

    def call(request_id, method, params):
        """Send a request and read stdout up to its reply; return the lines that aren't JSON."""
        send({"id": request_id, "method": method, "params": params})
        stray = []
        while True:
            line = server.stdout.readline()
            assert line, "the server closed stdout"
            try:
                message = json.loads(line)
            except ValueError:
                stray.append(line.rstrip())
                continue
            if message.get("id") == request_id:
                assert "result" in message, message
                return stray

    try:
        call(1, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        })
        send({"method": "notifications/initialized"})
        stray = call(2, "tools/call", {
            "name": "answer",
            "arguments": {"question": "Who is here?", "database": "d", "model": "gpt-4o-mini"},
        })
    finally:
        server.stdin.close()
        server.terminate()
        server.wait(timeout=10)
    assert stray == []
