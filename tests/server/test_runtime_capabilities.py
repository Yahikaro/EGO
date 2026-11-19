import importlib
import os
import sys
from pathlib import Path
import pytest
from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure


def capabilities():
    return importlib.import_module('runtime.adapters.capabilities')


def executable(tmp_path, help_text, version='version: b1234; CUDA : ON'):
    path = tmp_path/'llama fixture'
    path.write_text('#!'+sys.executable+'\nimport sys\nprint('+repr(help_text)+' if "--help" in sys.argv else '+repr(version)+')\n')
    path.chmod(0o700)
    return path


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable fixture')
def test_probe_actual_help_and_build_is_not_gpu_execution(tmp_path):
    binary = executable(tmp_path, '--threads N\n--batch-size N\n--flash-attn [on|off|auto]\n--model FNAME')
    result = capabilities().probe_binary(binary)
    assert result['status'] == 'PASS'
    assert '--threads' in result['flags']
    assert result['cuda_build'] is True
    assert result.get('gpu_execution') != 'VERIFIED'
    command = LlamaRuntime(RuntimeConfig(binary,tmp_path/'unused',threads=3,batch_size=64,flash_attention=False)).command()
    assert command[command.index('--threads')+1] == '3'
    assert command[command.index('--flash-attn')+1] == 'off'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable fixture')
def test_unknown_help_or_unsupported_values_fail_closed(tmp_path):
    binary = executable(tmp_path, 'unknown option --help')
    assert capabilities().probe_binary(binary)['status'] == 'FAIL'
    binary = executable(tmp_path, '--model FILE\n--threads N\n--flash-attn')
    with pytest.raises(RuntimeFailure, match='UNSUPPORTED_RUNTIME_OPTION'):
        LlamaRuntime(RuntimeConfig(binary,tmp_path/'unused',batch_size=32)).command()
    with pytest.raises(RuntimeFailure, match='UNSUPPORTED_RUNTIME_OPTION'):
        LlamaRuntime(RuntimeConfig(binary,tmp_path/'unused',flash_attention=False)).command()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable fixture')
def test_probe_bounds_output_and_timeout(tmp_path):
    binary = executable(tmp_path, 'x'*150000)
    assert capabilities().probe_binary(binary)['code'] == 'RUNTIME_PROBE_OUTPUT_LIMIT'
    binary.write_text('#!'+sys.executable+'\nimport time;time.sleep(10)\n')
    assert capabilities().probe_binary(binary, timeout=.05)['code'] == 'RUNTIME_PROBE_TIMEOUT'


def test_optional_defaults_and_ranges(tmp_path):
    from server.ego_core.config import CoreConfig
    config = CoreConfig(binary=str(tmp_path/'stale'),model=str(tmp_path/'stale.gguf'), threads=4, batch_size=128, flash_attention=True, temperature=.6,top_p=.9,top_k=30,repeat_penalty=1.1,max_tokens=77)
    runtime = config.runtime_config()
    assert runtime.threads == 4 and runtime.max_tokens == 77
    assert runtime.temperature == .6
    with pytest.raises(ValueError): config.validate_files()
    for values in ({'threads':0},{'batch_size':0},{'temperature':float('nan')},{'top_p':1.1},{'top_k':-1},{'repeat_penalty':0},{'max_tokens':4097}):
        with pytest.raises(ValueError): CoreConfig(**values)


def test_generation_uses_saved_defaults_and_explicit_token_override(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    received=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def do_GET(self):
            body=b'{"status":"ok"}'
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body=b'data: {"content":"ok","stop":true}\n\n'
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    class Alive:
        def poll(self): return None
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever); thread.start()
    r=LlamaRuntime(RuntimeConfig(Path(sys.executable),tmp_path/'unused',port=server.server_port,
                                temperature=.7,top_p=.9,top_k=12,repeat_penalty=1.2,max_tokens=77))
    r.process=Alive()
    try:
        assert list(r.stream('synthetic'))[-1].stop
        assert list(r.stream('synthetic',128))[-1].stop
        assert received[0]['n_predict']==77 and received[1]['n_predict']==128
        assert {k:received[0][k] for k in ('temperature','top_p','top_k','repeat_penalty')} == {'temperature':.7,'top_p':.9,'top_k':12,'repeat_penalty':1.2}
    finally:
        r.process=None
        server.shutdown();thread.join(2);server.server_close()

@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable fixture')
def test_wrapped_help_cannot_erase_declared_flash_attention_arity(tmp_path):
    binary=executable(tmp_path,'--model FILE\n-fa, --flash-attn [on|off|auto]\n    --flash-attn is mentioned again in an example')
    result=capabilities().probe_binary(binary)
    assert result['options']['--flash-attn']=={'takes_value':True,'values':['on','off','auto']}
    command=LlamaRuntime(RuntimeConfig(binary,tmp_path/'unused',flash_attention=False)).command()
    assert command[command.index('--flash-attn')+1]=='off'
