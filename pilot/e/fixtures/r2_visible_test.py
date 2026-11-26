def test_a_cut_answer_never_reaches_the_project(tmp_path: Path) -> None:
    """The provider cut the answer at the token cap: the diff parses, but it is half a change.

    The check is end-to-end on purpose: it runs the reference executor against a local endpoint
    that stops at ``finish_reason=length``, then the box, and it asks only what a person asks —
    the project did not move. It is the stand's visible check for the step loop; the hidden
    check is the record.
    """
    import http.server
    import threading

    project, box = make_box(tmp_path)
    body = json.dumps(
        {
            "choices": [{"message": {"content": DIFF}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
        }
    ).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        wrapper = tmp_path / "cut-executor.sh"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'export PANTHEON_MODEL_URL="http://127.0.0.1:{port}/chat/completions"\n'
            'export PANTHEON_MODEL_KEY="test"\n'
            'export PANTHEON_MODEL_PRICE_IN="0.27"\n'
            'export PANTHEON_MODEL_PRICE_OUT="1.10"\n'
            f'exec "{Path(__file__).resolve().parents[1] / "bin" / "pantheon-executor"}"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        write_profile(box, wrapper)
        task = write_task(tmp_path)
        assert dispatch(box, task) == 1
        assert (project / "src" / "app.py").read_text() == "value = 1\n"
    finally:
        server.shutdown()
