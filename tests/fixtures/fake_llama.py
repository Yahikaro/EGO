"""Test fixture only: protocol/lifecycle stub. Never a model or benchmark."""
import argparse
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--port', type=int)
p.add_argument('--host', choices=['127.0.0.1'])
p.add_argument('--load-mib', type=int, default=0)
p.add_argument('--ready-file', type=Path)
args, _ = p.parse_known_args()
if args.load_mib:
    allocation = bytearray(args.load_mib * 1024 * 1024)
    time.sleep(.3)
    del allocation


class Handler(BaseHTTPRequestHandler):
    # Explicit framing lets httpx finish the response then actively close its
    # connection. HTTP/1.0 + server EOF previously left server-side TIME_WAIT.
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def send_framed_headers(self, content_type, length):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.end_headers()

    def do_GET(self):
        payload = b'{"status":"ok"}'
        self.send_framed_headers('application/json', len(payload))
        self.wfile.write(payload)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/tokenize':
            payload = json.dumps({'tokens': [0] * len(data['content'].split())}).encode()
            self.send_framed_headers('application/json', len(payload))
            self.wfile.write(payload)
            return
        if self.path == '/v1/chat/completions':
            # OpenAI-compatible chat stream as emitted by llama-server: content
            # deltas, a finish_reason chunk, a usage-only chunk, then [DONE].
            prompt = [m for m in data['messages'] if m['role'] == 'user'][-1]['content']
            def chunk(body): return b'data: ' + json.dumps(body).encode() + b'\n\n'
            # Stage 4h: optional request log and canned memory-worker replies.
            if os.environ.get('EGO_FAKE_CHAT_LOG'):
                with open(os.environ['EGO_FAKE_CHAT_LOG'], 'a', encoding='utf-8') as log:
                    log.write(json.dumps(data['messages'], ensure_ascii=False) + '\n')
            system = next((m['content'] for m in data['messages'] if m['role'] == 'system'), '')
            memory_worker = system.startswith('Ты ведёшь') and 'память ролевой истории' in system
            if prompt == 'malformed':
                events = [b'data: invalid\n\n']
            else:
                count = 300 if os.environ.get('EGO_FAKE_SLOW_MEMORY') and memory_worker else 100 if prompt == 'slow' else 3
                if memory_worker and 'JSON' in system and not os.environ.get('EGO_FAKE_SLOW_MEMORY'):
                    deltas = ['["Пользователя зовут Тест", ', '"Торн боится воды"]']
                elif system.startswith('Ты помогаешь автору'):
                    deltas = ['Вот улучшенный текст:\n', 'Туманный порт Элдмур ', 'живёт торговлей и слухами.']
                elif memory_worker and not os.environ.get('EGO_FAKE_SLOW_MEMORY'):
                    deltas = ['Пользователь по имени Тест встретил Торна у реки и узнал его страхи.']
                else:
                    deltas = ['x'] * count
                events = [chunk({'choices': [{'index': 0, 'delta': {'content': d}, 'finish_reason': None}]}) for d in deltas]
                events += [chunk({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}),
                           chunk({'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': count},
                                  'timings': {'prompt_n': 5, 'predicted_n': count, 'predicted_ms': 30.0 * count,
                                              'predicted_per_second': 10}}),
                           b'data: [DONE]\n\n']
            self.send_framed_headers('text/event-stream', sum(len(event) for event in events))
            for event in events:
                try:
                    self.wfile.write(event)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(.03)
            return
        if data['prompt'] == 'malformed':
            events = [b'data: invalid\n\n']
        else:
            count = 100 if data['prompt'] == 'slow' else 3
            events = [b'data: {"content":"x","stop":false}\n\n'] * count
            events += [b'data: {"content":"", "stop":true,"tokens_predicted":3,"timings":{"predicted_per_second":10}}\n\n']
        self.send_framed_headers('text/event-stream', sum(len(event) for event in events))
        for event in events:
            try:
                self.wfile.write(event)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(.03)


class LoopbackServer(ThreadingHTTPServer):
    # HTTPServer inherits reuse=True even on Windows; never inherit it there.
    allow_reuse_address = os.name != 'nt'
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


with LoopbackServer((args.host, args.port), Handler) as server:
    # Tests that need a listener with no accepted connections must not create
    # TCP teardown state merely to observe that bind/listen has completed.
    if args.ready_file is not None:
        args.ready_file.write_text('listening', encoding='ascii')
    server.serve_forever()
