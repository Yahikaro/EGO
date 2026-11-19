"""Real Qt widget checks; offscreen execution is not native Windows validation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import time
from pathlib import Path
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from windows_ui.window import ControlCenterWindow

class FakeBackend:
    def __init__(self): self.calls=[]; self.started=threading.Event()
    def execute(self,action,*,payload=None,progress=None,cancel=None):
        self.calls.append((action,threading.current_thread().name,payload))
        if action=='status': return {'status':'PASS','code':'CORE_STATUS','core':{'state':'OFFLINE'}}
        if action=='monitor': return {'status':'PASS','code':'MONITOR_SNAPSHOT','details':{'cpu_percent':None,'gpus':[]}}
        if action=='smoke':
            self.started.set()
            if progress: progress({'stage':'LOADING_MODEL','message':'Loading','started_at':'now'})
            until=time.monotonic()+1
            while not cancel.is_set() and time.monotonic()<until: time.sleep(.01)
            return {'status':'CANCELLED' if cancel.is_set() else 'PASS','code':'TASK_CANCELLED' if cancel.is_set() else 'SMOKE_PASS'}
        return {'status':'PASS','code':'TASK_COMPLETED','log':None}
    def history(self): return []
    def logs(self,level='',search=''): return [{'file':'test.log','level':'INFO','text':'CORE_READY'}]
    def clean_logs(self,days,confirmed): return 0
    def export_diagnostics(self,path): Path(path).write_bytes(b'zip'); return Path(path)

@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    backend=FakeBackend(); value=ControlCenterWindow(tmp_path/'EGO пробел',backend=backend)
    value.show(); app.processEvents()
    yield value,backend,app
    value.poll_timer.stop(); value._allow_close=True; value.close(); value.deleteLater(); app.processEvents()

def spin(app,predicate,seconds=3):
    until=time.monotonic()+seconds
    while not predicate() and time.monotonic()<until: app.processEvents(); time.sleep(.01)
    assert predicate()

def test_navigation_has_six_tabs_and_resizes(window):
    value,backend,app=window
    # Six tabs (2026-11-18): General, Launch, Health, Access, Settings, Dependencies.
    from windows_ui.window import NAV
    assert value.pages.count()==len(NAV)==6
    for index in range(6):
        value.navigation.setCurrentRow(index); app.processEvents(); assert value.pages.currentIndex()==index
    for width,height in [(900,650),(1400,900)]: value.resize(width,height); app.processEvents()
    assert value.isVisible()

def test_model_paths_unicode_persist_and_reload_without_prompt(window):
    value,backend,app=window
    value.model_fields['binary'].setText('/tmp/LLM путь/llama-server.exe')
    value.model_fields['model'].setText('/tmp/Модель с пробелами.gguf')
    value.profile_name.setText('Локальная модель'); value.save_profile()
    snap=value.configuration.snapshot(); assert len(snap['profiles'])==1
    assert snap['profiles'][0]['values']['model']=='/tmp/Модель с пробелами.gguf'
    value.model_fields['model'].setText('unsaved'); value.reload_profiles()
    assert value.model_fields['model'].text().endswith('Модель с пробелами.gguf')

def test_smoke_worker_does_not_block_event_loop_and_can_cancel(window):
    value,backend,app=window
    value.run_action('smoke'); ticks=[]
    QTimer.singleShot(10,lambda:ticks.append(True))
    spin(app,lambda:backend.started.is_set() and bool(ticks))
    assert backend.calls[-1][1]!=threading.current_thread().name
    value.cancel_task(); spin(app,lambda:not value.busy)
    assert 'CANCELLED' in value.result_label.text()

def test_language_theme_and_close_preferences_survive(window):
    value,backend,app=window
    value.language_combo.setCurrentIndex(1); value.theme_combo.setCurrentIndex(1)
    value.close_combo.setCurrentIndex(1); value.save_preferences()
    snap=value.configuration.snapshot()
    assert snap['gui']['language']=='en' and snap['gui']['theme']=='light'
    assert snap['gui']['close_behavior']=='hide'
    assert value.navigation.item(0).text()=='General'

def test_hide_closing_never_stops_independent_core(window):
    value,backend,app=window
    value.configuration.save_gui({'close_behavior':'hide'})
    value.close(); app.processEvents()
    assert not value.isVisible()
    assert not any(call[0]=='stop' for call in backend.calls)
    value.show(); value.raise_(); app.processEvents(); assert value.isVisible()

def test_errors_show_original_network_codes(window):
    value,backend,app=window
    value._action_finished({'status':'FAIL','code':'PORT_IN_USE','errno':98,'winerror':10048})
    assert '10048' in value.result_text.toPlainText()
    assert 'PORT_IN_USE' in value.error_label.text()

def test_failed_stop_keeps_window_open(window):
    value,backend,app=window
    value._stop_close=True
    value._action_finished({'status':'FAIL','code':'CORE_STOP_TIMEOUT'})
    assert value.isVisible() and not value._allow_close

def test_setup_trial_runs_saved_profile_then_core_then_smoke(window):
    value,backend,app=window
    value.show_setup(); value.wizard.binary.setText('/tmp/llama-server')
    value.wizard.model.setText('/tmp/Модель с пробелами.gguf')
    value.wizard.trial()
    spin(app,lambda:backend.started.is_set())
    value.cancel_task(); spin(app,lambda:not value.busy)
    actions=[call[0] for call in backend.calls if call[0] not in {'status','monitor'}]
    assert actions==['apply_profile','initialize','start','smoke']
    assert 'CANCELLED' in value.wizard.report.toPlainText()
    assert value.wizard.trial_button.isEnabled()
    value.wizard.close()

def test_missing_model_check_runs_in_worker_and_reports_specific_code(window):
    value,backend,app=window
    value.model_fields['binary'].setText(str(Path(__file__)))
    value.model_fields['model'].setText('/definitely-missing/model.gguf')
    value.validate_model(); spin(app,lambda:not value.busy)
    assert 'MODEL_NOT_PROVIDED' in value.result_label.text()

def test_status_preserves_structured_original_socket_error(window):
    value,backend,app=window
    value._polled({'status_result':{'status':'PASS','core':{'runtime':'idle','last_runtime_error':{'code':'PORT_IN_USE','errno':98,'winerror':10048}}}})
    assert 'winerror=10048' in value.error_label.text()

def test_rejecting_setup_cancels_owned_task_and_never_continues_queue(window):
    value,backend,app=window
    entered=threading.Event(); release=threading.Event(); original=backend.execute
    def execute(action,**kwargs):
        if action=='apply_profile': entered.set(); release.wait(2)
        return original(action,**kwargs)
    backend.execute=execute
    value.show_setup(); value.wizard.binary.setText('/tmp/llama-server')
    value.wizard.model.setText('/tmp/model.gguf'); value.wizard.trial()
    spin(app,entered.is_set)
    value.wizard.reject(); release.set()
    spin(app,lambda:not value.busy)
    assert not any(call[0] in {'initialize','start','smoke'} for call in backend.calls)
    assert not value.wizard.isVisible()
