"""Disposable HTTP lifecycle fixture, never an inference benchmark."""
import argparse
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--host', choices=['127.0.0.1'])
parser.add_argument('--port', type=int)
parser.add_argument('--request-dir', type=Path, required=True)
args, _ = parser.parse_known_args()
slot = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def framed(self, code, body, kind='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self):
        self.framed(200, b'{"status":"ok"}')

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/tokenize':
            self.framed(200, json.dumps({'tokens': [0] * len(request['content'].split())}).encode())
            return
        chat = self.path == '/v1/chat/completions'
        prompt = [m for m in request['messages'] if m['role'] == 'user'][-1]['content'] if chat else request['prompt']
        if not slot.acquire(blocking=False):
            self.framed(503, b'{"error":"slot busy"}')
            return
        try:
            if chat and prompt not in {'block-body', 'block-headers', 'stuck-slot'}:
                body = (b'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': prompt}, 'finish_reason': None}]}).encode() + b'\n\n'
                        + b'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                                                  'timings': {'prompt_n': 3, 'predicted_n': 1, 'predicted_ms': 100.0,
                                                              'predicted_per_second': 10}}).encode() + b'\n\n'
                        + b'data: [DONE]\n\n')
                self.framed(200, body, 'text/event-stream')
                return
            if not chat and request.get('cache_prompt') is not False:
                self.framed(400, b'{"error":"cache_prompt must be false"}')
                return
            if prompt in {'block-body', 'block-headers', 'stuck-slot'}:
                if prompt != 'block-headers':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', '1000000')
                    self.end_headers()
                    self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n' if chat
                                     else b'data: {"content":"x","stop":false}\n\n')
                    self.wfile.flush()
                (args.request_dir / prompt).write_text('accepted')
                # No more tokens/headers: only a real peer disconnect can wake
                # this handler promptly. Emulate an unsupported stuck backend
                # separately, without claiming all llama versions cancel slots.
                self.connection.settimeout(20)
                try:
                    self.connection.recv(1)
                except (OSError, TimeoutError):
                    pass
                if prompt == 'stuck-slot':
                    time.sleep(1)
                self.close_connection = True
            else:
                payload = 'data: ' + json.dumps({'content': prompt, 'stop': True}) + '\n\n'
                self.framed(200, payload.encode(), 'text/event-stream')
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            slot.release()
            if prompt in {'block-body', 'block-headers', 'stuck-slot'}:
                (args.request_dir / (prompt + '.closed')).write_text('disconnected')


class Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != 'nt'
    allow_reuse_port = False
    daemon_threads = True

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


with Server((args.host, args.port), Handler) as server:
    server.serve_forever()
