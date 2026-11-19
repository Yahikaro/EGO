"""Qt-free local control operations. Call execute from a GUI worker thread.

Only trusted Core HTTPS commands control the independent server. Test children
are supervised separately and never grant ownership of an observed listener.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from server.ego_core import control
from server.ego_core.config import linked

STATUSES = {'PASS','FAIL','BLOCKED','SKIPPED','CANCELLED'}
SUITES = {'core_tests':'core','runtime_tests':'runtime','security_tests':'security','full_tests':'all'}
ACTIONS=set(SUITES)|{'initialize','auto_start','start','stop','restart','runtime_restart','smoke','environment','diagnostics','fixture_tests','benchmark','gpu_verify','apply_profile','restore_working','unknown'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def safe_code(value, fallback='CONTROL_INTERNAL_ERROR'):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',value) else fallback


def safe_path(path):
    return not any(linked(p) for p in (path,*path.parents))


def atomic_json(path, value):
    if not safe_path(path): raise ValueError('SETTINGS_PATH_UNSAFE')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.control-',delete=False) as output:
            temporary=Path(output.name)
            json.dump(value,output,ensure_ascii=True,indent=2)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary,path); temporary=None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


class ControlBackend:
    def __init__(self, root):
        self.root=Path(root).absolute()
        self._history_lock=threading.Lock()

    @staticmethod
    def _emit(progress,stage,message,**fields):
        if progress:
            progress({'stage':stage,'message':message,'started_at':timestamp(),**fields})

    def execute(self, action, *, payload=None, progress=None, cancel=None):
        started=time.monotonic(); stamp=timestamp()
        self._emit(progress,'VALIDATING','Checking local configuration.')
        try:
            if cancel is not None and cancel.is_set(): result={'status':'CANCELLED','code':'TASK_CANCELLED'}
            else: result=self._dispatch(action,payload or {},progress,cancel)
        except Exception as exc:
            result={'status':'BLOCKED' if getattr(exc,'blocked',False) else 'FAIL','code':safe_code(getattr(exc,'code',None),'CONFIG_INVALID' if isinstance(exc,ValueError) else 'CONTROL_INTERNAL_ERROR')}
            for key in ('errno','winerror'):
                value=getattr(exc,key,None)
                if type(value) is int: result[key]=value
            if isinstance(exc,control.ControlFailure):
                result.update({key:value for key,value in getattr(exc,'diagnostics',{}).items() if key in {'cause_code','errno','winerror'}})
        if not isinstance(result,dict): result={'status':'FAIL','code':'CONTROL_RESULT_INVALID'}
        result.setdefault('log',None)
        if result.get('status') not in STATUSES: result={'status':'FAIL','code':'CONTROL_RESULT_INVALID','log':None}
        result['duration_seconds']=round(time.monotonic()-started,3)
        stage='CANCELLED' if result['status']=='CANCELLED' else ('COMPLETED' if result['status']=='PASS' else 'FAILED')
        self._emit(progress,stage,safe_code(result.get('code')))
        if action not in {'status','monitor'}:
            try: self._record(action,result,stamp)
            except (OSError,ValueError): result['history_error']='HISTORY_WRITE_FAILED'
        return result

    def _dispatch(self,action,payload,progress,cancel):
        if action=='status': return self._status()
        if action=='initialize': return control.initialize(self.root)
        if action=='auto_start': return self._auto_start(progress,cancel)
        if action=='start':
            self._emit(progress,'STARTING_RUNTIME','Starting the independent Core server.')
            return control.start(self.root,cancel=cancel,progress=progress)
        if action=='stop': return self._stop()
        if action=='restart':
            control.stop(self.root)
            if cancel is not None and cancel.is_set(): return {'status':'CANCELLED','code':'TASK_CANCELLED'}
            return control.start(self.root,cancel=cancel,progress=progress)
        if action=='runtime_restart':
            core=control.request(self.root,'POST','/v1/runtime/restart',payload={},timeout=5)
            return {'status':'PASS','code':'RUNTIME_RESTART_REQUESTED','core':core}
        if action=='gpu_verify': return {'status':'BLOCKED','code':'GPU_OFFLOAD_NOT_VERIFIED','details':{'gpu_execution':'NOT_VERIFIED','reason':'Actual worker loading logs and matching process VRAM evidence are required.'}}
        if getattr(sys, 'frozen', False) and (action in SUITES or action in {'fixture_tests','environment','diagnostics'}):
            return {'status':'BLOCKED','code':'DEVELOPER_TOOLS_REQUIRE_SOURCE'}
        if action in SUITES:
            return self._run_tool(action,[sys.executable,str(self.root/'tools/windows/runner.py'),'test','--suite',SUITES[action]],progress,cancel,1800)
        if action=='fixture_tests':
            return self._run_tool(action,[sys.executable,str(self.root/'tools/windows/runner.py'),'benchmark','--fixture-check','--non-interactive'],progress,cancel,600)
        if action in {'environment','diagnostics'}:
            return self._run_tool(action,[sys.executable,str(self.root/'tools/windows/runner.py'),action],progress,cancel,90)
        if action=='monitor':
            from windows_ui.monitoring import snapshot
            return {'status':'PASS','code':'MONITOR_SNAPSHOT','details':snapshot(self.root)}
        if action=='smoke': return self._smoke(progress,cancel)
        if action=='benchmark':
            state=self._status()
            if state.get('code')=='CORE_STATUS':
                if state.get('core',{}).get('runtime_state') not in {'READY','LOADING','STARTING'}:
                    return {'status':'BLOCKED','code':'RUNTIME_NOT_READY'}
                return self._warm_benchmark(progress,cancel)
            if state.get('code') not in {'CORE_OFFLINE','CORE_UNINITIALIZED'}:
                return {'status':'BLOCKED','code':state.get('code','CORE_STATUS_FAILED')}
            if getattr(sys, 'frozen', False):
                return {'status':'BLOCKED','code':'DEVELOPER_TOOLS_REQUIRE_SOURCE'}
            config=self._model_config()
            return self._run_tool(action,[sys.executable,str(self.root/'tools/windows/runner.py'),'benchmark','--non-interactive','--binary',config.binary,'--model',config.model],progress,cancel,1800)
        if action in {'apply_profile','restore_working'}: return self._apply(action,payload,progress,cancel)
        return {'status':'BLOCKED','code':'ACTION_UNSUPPORTED'}

    def _auto_start(self,progress,cancel):
        """Initialize only absent identity/storage; control.start proves ownership."""
        from server.ego_core.config import CoreConfig,load_config,save_config
        from windows_ui.configuration import ConfigurationManager
        from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeFailure
        data=self.root/'data'
        if not safe_path(data): return {'status':'BLOCKED','code':'DATA_PATH_UNSAFE'}
        vault=data/control.KeyVault.VAULT
        if any(linked(data/name) for name in (control.KeyVault.VAULT,'main.db','profile.db')):
            return {'status':'BLOCKED','code':'DATA_PATH_UNSAFE'}
        if not vault.exists():
            if any((data/name).exists() for name in ('main.db','profile.db')):
                return {'status':'BLOCKED','code':'CORE_LOCKED'}
            data.mkdir(parents=True,exist_ok=True)
            # A lease excludes an already starting daemon from fresh setup.
            with control.CoreLease(data):
                result=control.initialize(self.root)
            if result.get('status')!='PASS': return result
        if cancel is not None and cancel.is_set(): return {'status':'CANCELLED','code':'TASK_CANCELLED'}
        config=load_config(data/'config.json')
        if config.model is None:
            snapshot=ConfigurationManager(self.root).snapshot()
            selected=next((row for row in snapshot['profiles'] if row['id']==snapshot['selected_id']),None)
            if selected is not None:
                try:
                    candidate=CoreConfig.model_validate(selected['values']); candidate.validate_files()
                    LlamaRuntime(candidate.runtime_config()).command()
                except (ValueError,OSError,RuntimeFailure): pass
                else:
                    # Verify the existing identity before changing its active
                    # configuration. Never rewrite a running Core's endpoint.
                    control.KeyVault(data).unlock()
                    try:
                        with control.CoreLease(data): save_config(data/'config.json',candidate)
                    except control.ControlFailure as exc:
                        if exc.code!='CORE_ALREADY_RUNNING': raise
        self._emit(progress,'STARTING_RUNTIME','Starting Core; a valid model preloads in the background.')
        return control.start(self.root,cancel=cancel,progress=progress)

    def _stop(self):
        data=self.root/'data'
        if not safe_path(data): return {'status':'BLOCKED','code':'DATA_PATH_UNSAFE'}
        if not data.exists(): return {'status':'PASS','code':'CORE_ALREADY_STOPPED'}
        if not (data/control.KeyVault.VAULT).exists() and not any((data/name).exists() for name in ('main.db','profile.db')):
            # A fresh project has no identity to authenticate. Prove no daemon
            # owns its data lease before treating it as already stopped.
            try:
                with control.CoreLease(data): pass
            except control.ControlFailure:
                return {'status':'BLOCKED','code':'CORE_LOCKED'}
            return {'status':'PASS','code':'CORE_ALREADY_STOPPED'}
        return control.stop(self.root)

    def _status(self):
        if not (self.root/'data'/control.KeyVault.VAULT).is_file():
            exists=any((self.root/'data'/name).exists() for name in ('main.db','profile.db'))
            return {'status':'BLOCKED','code':'CORE_LOCKED' if exists else 'CORE_UNINITIALIZED','core':{'state':'LOCKED' if exists else 'UNINITIALIZED'}}
        try:
            return {'status':'PASS','code':'CORE_STATUS','core':control.request(self.root,'GET','/v1/system/status',timeout=.8)}
        except Exception as exc:
            code=safe_code(getattr(exc,'code',None),'CONFIG_INVALID' if isinstance(exc,ValueError) else 'CORE_STATUS_FAILED')
            return {'status':'BLOCKED' if code!='CORE_OFFLINE' else 'PASS','code':code,'core':{'state':'OFFLINE' if code in {'CORE_OFFLINE','CORE_NETWORK_ERROR','CORE_TLS_FAILED'} else 'LOCKED'}}

    def _run_process(self,command,*,progress,cancel,timeout):
        """Supervise only this Popen and descendants whose identities we observed."""
        import psutil
        if cancel is not None and cancel.is_set(): return {'status':'CANCELLED','code':'TASK_CANCELLED','exit_code':None}
        process=subprocess.Popen(command,cwd=self.root,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
        try: owner=psutil.Process(process.pid) if os.name=='nt' and process.poll() is None else None
        except psutil.NoSuchProcess: owner=None
        owned={}; deadline=time.monotonic()+timeout; beat=0
        def track():
            if owner is None: return
            try:
                for child in owner.children(recursive=True): owned[child.pid]=(child,child.create_time())
            except psutil.Error: pass
        def stop_owned():
            if os.name!='nt':
                # Popen's new session is an OS ownership boundary. Avoid psutil
                # PIDs here: host /proc can expose a different PID namespace.
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: pass
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
                return
            track()
            children=[]
            for child,birth in owned.values():
                try:
                    if child.create_time()==birth:
                        child.terminate(); children.append(child)
                except psutil.Error: pass
            _,alive=psutil.wait_procs(children,timeout=.5)
            for child in alive:
                try:
                    if child.create_time()==owned[child.pid][1]: child.kill()
                except psutil.Error: pass
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            psutil.wait_procs(alive,timeout=1)
        try:
            while process.poll() is None:
                track()
                if cancel is not None and cancel.is_set():
                    stop_owned(); return {'status':'CANCELLED','code':'TASK_CANCELLED','exit_code':process.returncode}
                if time.monotonic()>=deadline:
                    stop_owned(); return {'status':'FAIL','code':'TASK_TIMEOUT','exit_code':process.returncode}
                if time.monotonic()>=beat:
                    self._emit(progress,'COLLECTING_METRICS','Local task is running; waiting for its measured report.',elapsed_seconds=round(timeout-(deadline-time.monotonic()),1)); beat=time.monotonic()+1
                if cancel is not None: cancel.wait(.05)
                else: time.sleep(.05)
            code=process.wait()
            return {'status':'PASS' if code==0 else 'FAIL','code':'TASK_COMPLETED' if code==0 else 'TASK_FAILED','exit_code':code}
        except BaseException:
            stop_owned(); raise

    def history(self):
        path=self.root/'settings/test-history.json'
        if not safe_path(path) or not path.is_file() or path.stat().st_size>262144: return []
        try:
            value=json.loads(path.read_bytes())
            if not isinstance(value,list): return []
            keys={'action','status','code','started_at','duration_seconds','log'}
            rows=[]
            for row in value[-100:]:
                if not isinstance(row,dict) or row.get('status') not in STATUSES or row.get('action') not in ACTIONS: continue
                if safe_code(row.get('code'),'')=='': continue
                stamp=row.get('started_at')
                if not isinstance(stamp,str) or len(stamp)>40: continue
                try:
                    if datetime.fromisoformat(stamp).tzinfo is None: continue
                except ValueError: continue
                duration=row.get('duration_seconds')
                if type(duration) not in (int,float) or not 0<=duration<=86400: continue
                log=row.get('log')
                if log is not None:
                    if not isinstance(log,str): continue
                    path=Path(log)
                    if path.parent!=self.root/'logs' or not re.fullmatch(r'\d{8}-\d{6}-\d{6}-gui-control\.json',path.name) or not safe_path(path): continue
                rows.append({k:v for k,v in row.items() if k in keys})
            return rows
        except (OSError,ValueError): return []

    def _record(self,action,result,started_at):
        with self._history_lock:
            rows=self.history()
            row={'action':action if action in ACTIONS else 'unknown',
                 'status':result['status'],'code':safe_code(result.get('code')),'started_at':started_at,'duration_seconds':result['duration_seconds'],'log':result['log']}
            logs=self.root/'logs'
            if not safe_path(logs): raise ValueError('LOGS_PATH_UNSAFE')
            logs.mkdir(exist_ok=True)
            name=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'-gui-control.json'
            atomic_json(logs/name,row)
            result['log']=str(logs/name); row['log']=str(logs/name)
            rows.append(row); atomic_json(self.root/'settings/test-history.json',rows[-100:])

    def _model_config(self):
        from server.ego_core.config import load_config
        try:
            config=load_config(self.root/'data/config.json')
            config.validate_files()
            return config
        except ValueError as exc:
            code=str(exc)
            failure=control.ControlFailure(code if code in {'MODEL_NOT_PROVIDED','RUNTIME_NOT_PROVIDED','INVALID_GGUF','CONFIG_PATH_UNSAFE'} else 'CONFIG_INVALID')
            failure.blocked=True
            raise failure from None

    def _smoke(self,progress,cancel):
        config=self._model_config()
        state=control.request(self.root,'GET','/v1/system/status',timeout=2)
        if state.get('runtime')=='unconfigured': return {'status':'BLOCKED','code':'MODEL_NOT_PROVIDED'}
        if state.get('active_generation'): return {'status':'BLOCKED','code':'CORE_BUSY'}
        if cancel is not None and cancel.is_set(): return {'status':'CANCELLED','code':'TASK_CANCELLED'}
        chat=control.request(self.root,'POST','/v1/chats',payload={'operation_id':str(uuid.uuid4()),'title':'EGO M1.1 diagnostic smoke'},timeout=3)
        generation=control.request(self.root,'POST','/v1/generations',payload={'operation_id':str(uuid.uuid4()),'chat_id':chat['id'],
            'content':'Reply with a short confirmation that local generation is available.','expected_version':chat['version']},timeout=3)['generation']
        generation_id=generation['id']; deadline=time.monotonic()+config.startup_timeout+90; beat=0
        self._emit(progress,'STARTING_RUNTIME','Waiting for local generation; runtime loading may be required.')
        try:
            while time.monotonic()<deadline:
                if cancel is not None and cancel.is_set():
                    return self._cancel_smoke(generation_id)
                result=control.request(self.root,'GET','/v1/generations/'+generation_id,timeout=3)
                if result.get('state') in {'completed','stopped','failed'}:
                    passed=result['state']=='completed'
                    outcome={'status':'PASS' if passed else 'FAIL','code':'CORE_GENERATION_SMOKE' if passed else safe_code(result.get('error_code'),'GENERATION_STOPPED'),
                        'generation_id':generation_id,
                        'output_characters':len(result.get('text','')),'gpu_execution':'NOT_VERIFIED','synthetic_chat_retained':True}
                    if passed:
                        from windows_ui.configuration import ConfigurationManager
                        manager=ConfigurationManager(self.root)
                        try: manager.mark_working(manager.snapshot().get('selected_id'))
                        except (ValueError,OSError): outcome['configuration_warning']='WORKING_CONFIGURATION_NOT_RECORDED'
                    return outcome
                if time.monotonic()>=beat:
                    self._emit(progress,'GENERATING','Waiting for the synthetic generation result; no token progress estimate is available.'); beat=time.monotonic()+1
                if cancel is not None: cancel.wait(.2)
                else: time.sleep(.2)
            stopped=self._cancel_smoke(generation_id)
            return {'status':'FAIL','code':'SMOKE_TIMEOUT','details':{'cancellation':stopped['code']}}
        except Exception:
            try: self._cancel_smoke(generation_id)
            except Exception: pass
            raise

    def _warm_benchmark(self,progress,cancel):
        # A cold benchmark would contend for the resident model's port/VRAM.
        # Measure one generation through its existing authenticated Core instead.
        result=self._smoke(progress,cancel)
        if result.get('status')!='PASS': return result
        state=control.request(self.root,'GET','/v1/system/status',timeout=3)
        metrics=state.get('generation_metrics') or {}
        measured=metrics.get('generation_id')==result.get('generation_id')
        speed=metrics.get('tokens_per_second') if measured else None
        result.update(status='PASS' if type(speed) in (int,float) else 'BLOCKED',
            code='WARM_RUNTIME_BENCHMARK' if type(speed) in (int,float) else 'BENCHMARK_METRICS_UNAVAILABLE')
        result['details']={'scope':'warm_runtime_generation','tokens_per_second':speed,
            'runtime_pid':state.get('runtime_pid'),'model_load_seconds':state.get('model_load_seconds')}
        if measured:
            for key in ('completion_tokens','prompt_tokens','generation_seconds'):
                result['details'][key]=metrics.get(key)
        atomic_json(self.root/'logs/latest-benchmark-report.json',{**result['details'],
            'status':result['status'],'code':result['code'],'gpu_execution':'NOT_VERIFIED'})
        return result

    def _cancel_smoke(self,generation_id):
        # This generation was created by this call; stop never targets another
        # user generation or shuts down the independent Core server.
        result=control.request(self.root,'POST','/v1/generations/'+generation_id+'/stop',payload={'operation_id':str(uuid.uuid4())},timeout=5)
        deadline=time.monotonic()+10
        while result.get('state') not in {'completed','stopped','failed'} and time.monotonic()<deadline:
            time.sleep(.1)
            result=control.request(self.root,'GET','/v1/generations/'+generation_id,timeout=2)
        if result.get('state') not in {'completed','stopped','failed'}: return {'status':'FAIL','code':'SMOKE_CANCEL_RESULT_UNKNOWN'}
        return {'status':'CANCELLED','code':'SMOKE_CANCELLED','gpu_execution':'NOT_VERIFIED','synthetic_chat_retained':True}

    def _apply(self,action,payload,progress,cancel):
        from windows_ui.configuration import ConfigurationManager
        from server.ego_core.config import CoreConfig,load_config,save_config
        from runtime.adapters.llama_cpp import LlamaRuntime
        manager=ConfigurationManager(self.root); snapshot=manager.snapshot()
        profile_id=payload.get('id')
        if action=='restore_working':
            values=manager.working_config()
            if values is None: return {'status':'BLOCKED','code':'NO_WORKING_CONFIGURATION'}
        else:
            profile=next((row for row in snapshot['profiles'] if row['id']==profile_id),None)
            if profile is None: return {'status':'BLOCKED','code':'PROFILE_NOT_FOUND'}
            values=profile['values']
        try:
            candidate=CoreConfig.model_validate(values); candidate.validate_files()
            LlamaRuntime(candidate.runtime_config()).command()
        except ValueError as exc:
            return {'status':'BLOCKED','code':safe_code(str(exc),'CONFIG_INVALID')}
        state=self._status()
        running=state.get('code')=='CORE_STATUS'
        if state.get('code') not in {'CORE_STATUS','CORE_OFFLINE','CORE_UNINITIALIZED'}: return {'status':'BLOCKED','code':state['code']}
        path=self.root/'data/config.json'
        if not safe_path(path): return {'status':'BLOCKED','code':'CONFIG_PATH_UNSAFE'}
        old=load_config(path) if path.exists() else None
        core_restart=running and (old is None or any(getattr(old,key)!=getattr(candidate,key) for key in ('host','api_port','local_http')))
        if core_restart and payload.get('restart') is not True: return {'status':'BLOCKED','code':'RESTART_CONFIRMATION_REQUIRED'}
        if cancel is not None and cancel.is_set(): return {'status':'CANCELLED','code':'TASK_CANCELLED'}
        if core_restart: control.stop(self.root)
        candidate_started=False; configured=False; selection_changed=False
        try:
            # Commit exactly the immutable object whose files/CLI were checked.
            # The GUI can save a different registry revision while stop waits.
            path.parent.mkdir(parents=True,exist_ok=True)
            save_config(path,candidate)
            if core_restart:
                self._emit(progress,'STARTING_RUNTIME','Starting Core with the validated candidate configuration.')
                result=control.start(self.root)
                if result.get('status')!='PASS': raise control.ControlFailure(safe_code(result.get('code'),'CORE_START_FAILED'))
                candidate_started=True
            elif running:
                # Finish fallible local metadata writes before the asynchronous
                # reload is accepted: a loading Runtime cannot roll back yet.
                if action=='apply_profile':
                    manager.select_profile(profile_id); selection_changed=True
                control.request(self.root,'POST','/v1/runtime/configure',payload=candidate.model_dump(),timeout=5)
                configured=True
            if action=='apply_profile' and not selection_changed: manager.select_profile(profile_id)
            return {'status':'PASS','code':'WORKING_CONFIGURATION_RESTORED' if action=='restore_working' else 'PROFILE_APPLIED','details':{'restarted':core_restart,'runtime_configured':configured,'working_verified':False}}
        except Exception as exc:
            rollback='PASS'
            try:
                if candidate_started: control.stop(self.root)
                if old is not None: save_config(path,old)
                elif path.exists(): path.unlink()
                if selection_changed:
                    from windows_ui.configuration import _LOCK,_write
                    with _LOCK:
                        registry=manager._registry()
                        previous=snapshot['selected_id']
                        if previous is not None: manager._find(registry,previous)
                        registry['selected_id']=previous
                        _write(manager.registry_path,registry)
                if core_restart: control.start(self.root)
                elif configured and old is not None:
                    control.request(self.root,'POST','/v1/runtime/configure',payload=old.model_dump(),timeout=5)
            except Exception: rollback='FAIL'
            if not configured and not core_restart and getattr(exc,'code',None)=='CORE_BUSY' and rollback=='PASS':
                return {'status':'BLOCKED','code':'CORE_BUSY'}
            return {'status':'FAIL','code':'PROFILE_APPLY_FAILED','details':{'cause_code':safe_code(getattr(exc,'code',None),'CONFIG_APPLY_FAILED'),'rollback_status':rollback}}

    def logs(self,level='',search=''):
        from windows_ui.diagnostics import logs
        return logs(self.root,level=level,search=search)

    def export_diagnostics(self,destination):
        from windows_ui.diagnostics import export_diagnostics
        return export_diagnostics(self.root,Path(destination))

    def clean_logs(self,days,confirmed=False):
        from windows_ui.diagnostics import clean_logs
        return clean_logs(self.root,days=days,confirmed=confirmed)

    def _run_tool(self,action,command,progress,cancel,timeout):
        started=time.time()
        result=self._run_process(command,progress=progress,cancel=cancel,timeout=timeout)
        return self._task_report(action,result,started)

    def _task_report(self,action,result,started):
        """Read only fresh, bounded, projected toolkit reports; exit always wins."""
        from windows_ui.diagnostics import _projection,_text_projection
        from tools.windows.runner import parse_junit
        if result.get('status')=='CANCELLED': return result
        name=('latest-test-report.txt' if action in SUITES else
              'latest-benchmark-report.json' if action in {'benchmark','fixture_tests'} else
              'latest-diagnostics.json' if action=='diagnostics' else None)
        if name is None: return result
        path=self.root/'logs'/name
        try:
            if not safe_path(path) or not path.is_file() or path.stat().st_size>131072 or path.stat().st_mtime<started: return result
            with path.open('rb') as stream: raw=stream.read(131073)
            if len(raw)>131072: return result
            if name.endswith('.json'): details=_projection(json.loads(raw))
            else: details=_text_projection(raw.decode('utf-8',errors='replace'))
            if action in SUITES:
                junit=self.root/'logs/latest-test-report.xml'
                if safe_path(junit) and junit.is_file() and junit.stat().st_mtime>=started:
                    parsed=parse_junit(junit)
                    details['junit']={key:parsed[key] for key in ('valid','passed','failed','skipped')}
            if details: result['details']=details
            if result.get('exit_code') not in (None,0): result['status']='FAIL'
            elif details.get('status') in {'FAIL','BLOCKED','SKIPPED'}: result['status']=details['status']
            if action in SUITES and details.get('passed')==0 and details.get('failed')==0 and details.get('skipped',0)>0 and result.get('exit_code')==0: result['status']='SKIPPED'
            if action=='fixture_tests':
                result.setdefault('details',{}).update(scope='fixture_contract_checks_only',real_model_performance='NOT_TESTED')
        except (OSError,ValueError): result['report_warning']='TASK_REPORT_UNAVAILABLE'
        return result
