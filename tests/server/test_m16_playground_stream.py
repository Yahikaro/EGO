import time
from server.ego_core import control
from windows_ui.playground import _generation_updates


def test_client_prefers_sse_and_reads_terminal(monkeypatch):
    def stream(*args, **kwargs):
        yield {'id':'x','state':'streaming','text':'one'}
        yield {'id':'x','state':'completed','text':'one two'}
    monkeypatch.setattr(control,'generation_event_stream',stream)
    monkeypatch.setattr(control,'request',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('should not poll')))
    out=list(_generation_updates('unused','x',time.monotonic()+1))
    assert [r['state'] for r in out]==['streaming','completed']


def test_disconnect_falls_back_without_losing_partial_text(monkeypatch):
    def broken(*args, **kwargs):
        yield {'id':'x','state':'streaming','text':'half'}
        raise control.ControlFailure('CORE_STREAM_INTERRUPTED')
    monkeypatch.setattr(control,'generation_event_stream',broken)
    monkeypatch.setattr(control,'request',lambda *a,**kw:{'id':'x','state':'completed','text':'full'})
    out=list(_generation_updates('unused','x',time.monotonic()+1))
    assert [r['text'] for r in out]==['half','full']
