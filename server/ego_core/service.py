"""Durable generations sharing one Core-owned persistent local runtime."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import os
import threading
import time
import uuid
from runtime.adapters.llama_cpp import LlamaRuntime, RuntimeConfig, RuntimeFailure
from server.ego_core.store import StoreFailure
from server.ego_core.roleplay_prompt import compile_messages, strip_speaker_prefix
from server.ego_core.content_guard import output_blocked
from server.ego_core import __version__

TERMINAL = {'completed', 'stopped', 'failed'}
STREAM_SNAPSHOT_HZ = 8  # Max encrypted draft writes per second during streaming.
RUNTIME_STATES = {'STOPPED','STARTING','LOADING','READY','BUSY','STOPPING','ERROR'}
_LOADING_FIELDS = ('port','context','gpu_layers','threads','batch_size','flash_attention')


class ContentBlocked(Exception):
    """The model's reply crossed a hard content limit (content_guard.output_blocked)."""


class CoreFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CoreService:
    def __init__(self, store, *, runtime=None, runtime_factory=None):
        self.store, self.runtime = store, runtime
        self._runtime_factory = runtime_factory or (type(runtime) if runtime is not None else LlamaRuntime)
        self._started_at = time.monotonic()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._close_lock = threading.Lock()
        self._active = None
        self._pending = []
        self._loader = None
        self._closed = False
        self._stopping = set()
        self._runtime_state = 'STOPPED'
        self._runtime_events = []
        self.installation_id = None
        self.runtime_diagnostic_callback = None
        self.last_runtime_error = None
        self.last_api_error = None
        self._last_generation_metrics = None
        self._model_load_seconds = None
        self.recovered = store.recover_interrupted()
        # Stage 4h: per-chat memory, only over the real encrypted store.
        self.memory = None
        self.profile = None
        if hasattr(store, '_transaction'):
            from server.ego_core.memory import ChatMemory
            from server.ego_core.profile import OwnerProfile
            self.memory = ChatMemory(store)
            self.profile = OwnerProfile(store)
        with self._condition: self._transition('STOPPED')

    def _transition(self, state, diagnostics=None):
        self._runtime_state = state
        event={'state':state,'timestamp':datetime.now(timezone.utc).isoformat()}
        if diagnostics:
            event.update({k:v for k,v in diagnostics.items() if k in {'code','errno','winerror','exit_code','operation'} and v is not None})
        self._runtime_events.append(event)
        self._runtime_events[:]=self._runtime_events[-32:]
        self._condition.notify_all()
        if state=='ERROR' and self.runtime_diagnostic_callback:
            try: self.runtime_diagnostic_callback(dict(event))
            except Exception: pass

    @staticmethod
    def _error(exc):
        # Only allow operational codes and numeric OS diagnostics across API/logs.
        import re
        code=getattr(exc,'code',None)
        if not isinstance(code,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',code):
            code=str(exc) if isinstance(exc,ValueError) else 'RUNTIME_INTERNAL_ERROR'
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',code): code='CONFIG_INVALID' if isinstance(exc,ValueError) else 'RUNTIME_INTERNAL_ERROR'
        error={'code':code}
        for key in ('errno','winerror','exit_code'):
            value=getattr(exc,key,None)
            if type(value) is int: error[key]=value
        operation=getattr(exc,'operation',None)
        if operation in {'socket','setsockopt','bind','spawn','start','shutdown_write','close'}: error['operation']=operation
        return error

    def _pid(self):
        process=getattr(self.runtime,'process',None)
        return process.pid if process is not None and process.poll() is None else None

    def _observed_crash(self):
        if self._runtime_state in {'READY','BUSY'} and hasattr(self.runtime,'process') and self._pid() is None:
            self.last_runtime_error={'code':'RUNTIME_EXITED'}
            self._transition('ERROR',self.last_runtime_error)

    def status(self):
        with self._condition:
            self._observed_crash()
            return {'stage':'M1','version':__version__,'storage':'ready',
                    'installation_id':self.installation_id,
                    'uptime_seconds':max(0.,time.monotonic()-self._started_at),
                    # Legacy scheduling alias. Use runtime_state for readiness.
                    'runtime':'unconfigured' if self.runtime is None else ('busy' if self._active else 'idle'),
                    'runtime_state':self._runtime_state,'runtime_pid':self._pid(),
                    'model_ready':self._runtime_state in {'READY','BUSY'},
                    'runtime_events':[dict(row) for row in self._runtime_events],
                    'active_generation':self._active[0] if self._active and not self._is_helper_job(self._active) else None,
                    'memory_job':self._active[0][7:] if self._active and self._is_memory_job(self._active) else None,
                    'enchant_job':bool(self._active and str(self._active[0]).startswith('enchant:')),
                    'queued_generations':[item[0]['id'] for item in self._pending],
                    'recovered_generations':self.recovered,
                    'last_runtime_error':dict(self.last_runtime_error) if self.last_runtime_error else None,
                    'last_api_error':self.last_api_error,
                    'generation_metrics':dict(self._last_generation_metrics) if self._last_generation_metrics else None,
                    'runtime_context_limit':getattr(getattr(self.runtime,'config',None),'context',None),
                    'model_load_seconds':self._model_load_seconds,
                    'scope':'local developer completions; no character/profile engine'}

    def model_info(self):
        """Stable client-facing loaded-model metadata; never expose local paths."""
        with self._condition:
            self._observed_crash()
            config = getattr(self.runtime, 'config', None)
            model = getattr(config, 'model', None)
            return {'state': self._runtime_state,
                    'name': model.name if model is not None else None,
                    'configured': config is not None,
                    'ready': self._runtime_state in {'READY', 'BUSY'},
                    'busy': self._active is not None,
                    'context': getattr(config, 'context', None),
                    'max_tokens': getattr(config, 'max_tokens', None)}

    def _launch_loader(self, *, replacement=None, replace=False, restart=False):
        # Called only while owning _condition. Thread pointer and transition are
        # published before the thread can acquire the same lock.
        if self._closed: raise CoreFailure('CORE_STOPPING')
        self._transition('STOPPING' if restart or replace else 'STARTING')
        thread=threading.Thread(target=self._load,args=(replacement,replace,restart),daemon=True,name='ego-runtime-load')
        self._loader=thread
        thread.start()

    def start_runtime(self):
        with self._condition:
            if self._closed: raise CoreFailure('CORE_STOPPING')
            # ERROR deliberately requires explicit restart/configuration, not a
            # timer or another message triggering an unbounded reload loop.
            if self.runtime is not None and self._runtime_state=='STOPPED' and self._loader is None:
                self._launch_loader()
            return self.status()

    def restart_runtime(self):
        with self._condition:
            if self._closed: raise CoreFailure('CORE_STOPPING')
            if self._active or self._loader is not None: raise CoreFailure('CORE_BUSY')
            if self.runtime is None: raise CoreFailure('MODEL_NOT_PROVIDED')
            self._launch_loader(restart=True)
            return self.status()

    @staticmethod
    def _loading_identity(config):
        if config is None: return None
        return (os.path.normcase(str(config.binary.resolve())),os.path.normcase(str(config.model.resolve())),
                *(getattr(config,key) for key in _LOADING_FIELDS))

    def reconfigure_runtime(self, config):
        if config is not None and not isinstance(config,RuntimeConfig): raise CoreFailure('CONFIG_INVALID')
        with self._condition:
            if self._closed: raise CoreFailure('CORE_STOPPING')
            if self._active or self._loader is not None: raise CoreFailure('CORE_BUSY')
            current=getattr(self.runtime,'config',None)
            if config is None and self.runtime is None: return self.status()
            if current is not None and self._loading_identity(current)==self._loading_identity(config):
                self.runtime.config=config
                return {**self.status(),'runtime_change':'request_options'}
            self._launch_loader(replacement=config,replace=True)
            return {**self.status(),'runtime_change':'model_loading'}

    def _load(self, replacement, replace, restart):
        runtime=self.runtime
        try:
            if (replace or restart) and runtime is not None: runtime.shutdown()
            with self._condition:
                if self._closed: return
                if replace:
                    previous=runtime
                    runtime=self._runtime_factory(replacement) if replacement is not None else None
                    # Replacing the adapter must not forget that THIS Core just
                    # reaped this configured endpoint (Windows TCP teardown).
                    if (isinstance(previous,LlamaRuntime) and isinstance(runtime,LlamaRuntime)
                            and previous.config.port==runtime.config.port):
                        runtime._last_reaped_at=previous._last_reaped_at
                    self.runtime=runtime
                if runtime is None:
                    self._transition('STOPPED'); return
                self._transition('STARTING')
                self._model_load_seconds=None
                self._transition('LOADING')
            started=time.monotonic()
            runtime.start()
            with self._condition:
                if not self._closed:
                    self._model_load_seconds=round(time.monotonic()-started,3)
                    self.last_runtime_error=None
                    self._loader=None
                    self._transition('READY')
        except Exception as exc:
            with self._condition:
                if not self._closed:
                    self.last_runtime_error=self._error(exc)
                    self._transition('ERROR',self.last_runtime_error)
        finally:
            if self._closed and runtime is not None:
                try: runtime.shutdown()
                except Exception: pass
            with self._condition:
                if self._loader is threading.current_thread(): self._loader=None
                self._condition.notify_all()

    def _wait_ready(self, stop):
        with self._condition:
            if self._runtime_state=='STOPPED' and not self._closed:
                self._launch_loader()
            timeout=getattr(getattr(self.runtime,'config',None),'startup_timeout',120.)+5
            deadline=time.monotonic()+timeout
            while self._runtime_state in {'STARTING','LOADING'}:
                if stop.is_set() or self._closed: return False
                remaining=deadline-time.monotonic()
                if remaining<=0: raise RuntimeFailure('STARTUP_TIMEOUT')
                self._condition.wait(min(.05,remaining))
            if stop.is_set() or self._closed: return False
            if self._runtime_state!='READY':
                raise CoreFailure((self.last_runtime_error or {}).get('code','RUNTIME_NOT_READY'))
            self._transition('BUSY')
            return True

    def _check_accepting(self):
        """One error contract for generate/regenerate/continue. Caller owns _condition."""
        if self._closed: raise CoreFailure('CORE_STOPPING')
        self._observed_crash()
        if self._runtime_state=='STOPPING': raise CoreFailure('CORE_BUSY')
        if self.runtime is None: raise CoreFailure('MODEL_NOT_PROVIDED')
        if self._runtime_state=='ERROR': raise CoreFailure('RUNTIME_NOT_READY')

    def generate(self, operation_id, chat_id, content, expected_version, character_name=None, character_description=None):
        # Trusted runtime instruction supplied explicitly by the GUI, never persisted as chat text.
        if character_name is not None or character_description is not None:
            if not isinstance(self.runtime, LlamaRuntime): raise CoreFailure("CHARACTER_REQUIRES_LLAMA")
            from server.ego_core.roleplay_prompt import compile_messages
            compile_messages([{"role":"user","content":"validation","status":"completed"}], character_name=character_name, character_description=character_description)
        with self._condition:
            if self._closed: raise CoreFailure('CORE_STOPPING')
            self._observed_crash()
            try: previous=self.store.operation(operation_id)
            except StoreFailure as exc:
                if exc.code!='OPERATION_NOT_FOUND': raise
                previous=None
            if previous is None:
                self._check_accepting()
            generation=self.store.begin_generation(operation_id,chat_id,content,expected_version)
            if generation['state']!='queued' or (self._active and self._active[0]==generation['id']): return generation
            self._pending.append((generation, character_name, character_description))
            self._start_next_locked()
            return generation

    def regenerate(self, operation_id, chat_id, expected_version, character_name=None, character_description=None):
        if self.runtime is not None and not isinstance(self.runtime,LlamaRuntime): raise CoreFailure('REGENERATION_REQUIRES_LLAMA')
        if character_name is not None or character_description is not None:
            if not isinstance(self.runtime,LlamaRuntime): raise CoreFailure('CHARACTER_REQUIRES_LLAMA')
            compile_messages([{'role':'user','content':'validation','status':'completed'}],
                                  character_name=character_name,character_description=character_description)
        with self._condition:
            self._check_accepting()
            generation=self.store.begin_regeneration(operation_id,chat_id,expected_version)
            if generation['state']!='queued': return generation
            self._pending.append((generation, character_name, character_description))
            self._start_next_locked()
            return generation

    def continue_generation(self, operation_id, chat_id, expected_version, character_name=None, character_description=None):
        if self.runtime is not None and not isinstance(self.runtime,LlamaRuntime): raise CoreFailure('CONTINUATION_REQUIRES_LLAMA')
        if character_name is not None or character_description is not None:
            compile_messages([{'role':'user','content':'validation','status':'completed'}],
                                  character_name=character_name,character_description=character_description)
        with self._condition:
            self._check_accepting()
            generation=self.store.begin_continuation(operation_id,chat_id,expected_version)
            if generation['state']!='queued': return generation
            self._pending.append((generation,character_name,character_description,True))
            self._start_next_locked()
            return generation

    @staticmethod
    def _is_memory_job(active):
        return isinstance(active[0], str) and active[0].startswith('memory:')

    @staticmethod
    def _is_helper_job(active):
        return isinstance(active[0], str) and active[0].startswith(('memory:', 'enchant:'))

    def _start_next_locked(self):
        """Only one llama request runs at a time; queued work stays durable."""
        if self._active and self._pending and self._is_helper_job(self._active) and not self._active[1].is_set():
            # A user reply always wins over background memory work: cancel it;
            # the memory thread calls back here when it has released the runtime.
            self._active[1].set()
            try: self._cancel_generation(self._active)
            except Exception: pass
            return
        if self._closed or self._active or self._runtime_state in {'STOPPING','ERROR'}:
            return
        while self._pending:
            queued=self._pending.pop(0)
            generation, name, description = queued[:3]
            continuing = len(queued)>3 and queued[3]
            latest = self.store.generation(generation['id'])
            if latest['state'] != 'queued':
                continue
            if latest.get('stop_requested'):
                self.store.set_generation_state(generation['id'],'stopped')
                continue
            stop = threading.Event()
            thread = threading.Thread(target=self._run,
                args=(generation,stop,name,description,True) if continuing else (generation,stop,name,description),daemon=True,name='ego-generation')
            self._active = (generation['id'],stop,thread)
            thread.start()
            return

    def _prompt(self, chat_id):
        # Only this chat's persisted messages. No profile service/reader is a
        # dependency. Model-specific character templates belong to M3.
        messages = self.store.messages(chat_id)
        if len(messages) == 1:
            return messages[0]['content']
        # Reserve the complete newest accepted input first. Role labels and
        # history must never evict a valid user message at the size boundary.
        newest = messages[-1]['content']
        current, suffix = 'user: ' + newest, '\nassistant:'
        if len(current) + len(suffix) > 32768:
            return newest
        parts, used = [current], len(current) + len(suffix)
        for message in reversed(messages[:-1]):
            if message['status'] not in {'completed', 'persisted'}:
                continue
            part = message['role'] + ': ' + message['content']
            if used + len(part) + 1 > 32768:
                break
            parts.append(part)
            used += len(part) + 1
        return '\n'.join(reversed(parts)) + suffix

    @staticmethod
    def _runtime_budget(runtime, requested=None):
        config=getattr(runtime,'config',None)
        window=getattr(config,'context',2048)
        requested=getattr(config,'max_tokens',512) if requested is None else requested
        if type(window) is not int or window<512 or type(requested) is not int or requested<1:
            raise ValueError('INVALID_RUNTIME_CONTEXT')
        return window,min(max(32,requested),window-256)

    def _run(self, generation, stop, character_name=None, character_description=None, continuing=False):
        identity=generation['id']
        text,state,code='', 'completed', None
        metrics=None
        finish_reason=None
        started_at=time.monotonic()
        def visible(raw):
            return strip_speaker_prefix(raw, (character_name or '').strip()) if character_name else raw
        if self.memory is not None and character_name:
            self.memory.note_character(generation['chat_id'], character_name.strip())
        try:
            self.store.set_generation_state(identity,'started')
            if not self._wait_ready(stop): state='stopped'
            else:
                ended=False
                written_at=0.
                with closing(self._open_stream(generation,identity,character_name,character_description,continuing)) as events:
                    for event in events:
                        if stop.is_set(): break
                        if len(text)+len(event.content)>262144: raise CoreFailure('OUTPUT_LIMIT_EXCEEDED')
                        text+=event.content
                        # Hard limits on the reply itself: stop now, save nothing.
                        if event.content and output_blocked(text): raise ContentBlocked()
                        if event.stop: finish_reason=getattr(event,'finish_reason',None)
                        if event.stop and isinstance(getattr(event,'timings',None),dict):
                            metrics=self._extract_timings(event.timings,getattr(event,'tokens_predicted',None))
                        # Throttle encrypted draft snapshots: every token used to
                        # cost a fsync'd write of the whole text. SSE/polling
                        # clients still receive ~STREAM_SNAPSHOT_HZ updates/s and
                        # the terminal state below always carries the full text.
                        now=time.monotonic()
                        if event.content and not event.stop and now-written_at>=1/STREAM_SNAPSHOT_HZ:
                            self.store.set_generation_state(identity,'streaming',text=visible(text))
                            written_at=now
                        if event.stop: ended=True; break
                if stop.is_set(): state='stopped'
                elif not ended: raise RuntimeFailure('STREAM_INTERRUPTED')
        except ContentBlocked:
            # Not a runtime fault: the model stays READY, the draft is discarded.
            state,code,text='failed','CONTENT_BLOCKED',''
        except ValueError as exc:
            budget=str(exc) in {'CONTEXT_BUDGET_EXCEEDED','Missing latest user message'}
            state,code=('stopped',None) if stop.is_set() else ('failed','CONTEXT_BUDGET_EXCEEDED' if budget else 'CONFIG_INVALID')
        except (RuntimeFailure,CoreFailure) as exc:
            state,code=('stopped',None) if stop.is_set() else ('failed',exc.code)
            if state=='failed':
                with self._condition:
                    self.last_runtime_error=self._error(exc)
                    if not self._closed: self._transition('ERROR',self.last_runtime_error)
        except Exception:
            state,code=('stopped',None) if stop.is_set() else ('failed','RUNTIME_INTERNAL_ERROR')
            if state=='failed':
                with self._condition:
                    self.last_runtime_error={'code':code}
                    if not self._closed: self._transition('ERROR',self.last_runtime_error)
        finally:
            text=visible(text)
            if state=='completed':
                elapsed=round(time.monotonic()-started_at,3)
                with self._condition:
                    self._last_generation_metrics={'generation_id':identity,
                         'request_seconds':elapsed,
                         'finish_reason':finish_reason,'generated_characters':len(text),
                         **(metrics or {})}
                    if self._last_generation_metrics.get('tokens_per_second') is None:
                        self._last_generation_metrics['average_request_tokens_per_second'] = self._request_speed(
                            self._last_generation_metrics.get('completion_tokens'), elapsed)
            if state=='completed' and hasattr(self.store,'set_generation_meta'):
                # Before the terminal write, so the final snapshot already carries it.
                try: self.store.set_generation_meta(identity,finish_reason,(metrics or {}).get('completion_tokens'))
                except Exception: pass
            try:
                self.store.set_generation_state(identity,state,text=text,error_code=code)
            finally:
                with self._condition:
                    if self._active and self._active[0]==identity:
                        if not self._closed and self._runtime_state=='BUSY':
                            if hasattr(self.runtime,'process') and self._pid() is None:
                                self.last_runtime_error={'code':'RUNTIME_EXITED'}
                                self._transition('ERROR',self.last_runtime_error)
                            else: self._transition('READY')
                        self._active=None
                        self._start_next_locked()
                        if state=='completed' and not self._active:
                            self._start_memory_locked(generation['chat_id'])
                        self._condition.notify_all()

    # ---- stage 4h: background memory extraction between replies ---------------------
    def _start_memory_locked(self, chat_id, force=False):
        """Caller owns _condition. Starts only on an idle, READY llama runtime."""
        if (self.memory is None or self._closed or self._active or self._pending or self._loader is not None
                or not isinstance(self.runtime, LlamaRuntime) or self._runtime_state != 'READY'):
            return False
        if not force and not self.memory.due(chat_id):
            return False
        stop = threading.Event()
        thread = threading.Thread(target=self._run_memory, args=(chat_id, stop, force), daemon=True, name='ego-memory')
        self._transition('BUSY')
        self._active = ('memory:' + chat_id, stop, thread)
        thread.start()
        return True

    def delete_chat(self, chat_id):
        """Delete a chat unless a reply or a memory job for it is running."""
        with self._condition:
            for item in self._pending:
                if item[0].get('chat_id') == chat_id:
                    raise CoreFailure('GENERATION_BUSY')
            if self._active and self._is_memory_job(self._active) and self._active[0] == 'memory:' + chat_id:
                self._active[1].set()
                try: self._cancel_generation(self._active)
                except Exception: pass
                raise CoreFailure('CORE_BUSY')
            return self.store.delete_chat(chat_id)

    def extract_memory_now(self, chat_id):
        with self._condition:
            self._observed_crash()
            if self._closed: raise CoreFailure('CORE_STOPPING')
            if self.memory is None or not isinstance(self.runtime, LlamaRuntime): raise CoreFailure('MEMORY_UNAVAILABLE')
            if self._active or self._pending: raise CoreFailure('CORE_BUSY')
            if self._runtime_state != 'READY': raise CoreFailure('RUNTIME_NOT_READY')
            self.memory.view(chat_id)                     # NOT_FOUND for a foreign/unknown chat
            if not self._start_memory_locked(chat_id, force=True): raise CoreFailure('CORE_BUSY')
        return {'chat_id': chat_id, 'status': 'started'}

    def _collect(self, messages, max_tokens, stop, sampling=None):
        text = ''
        sampling = sampling or {'temperature': 0.2, 'top_p': 0.9, 'top_k': 40, 'min_p': 0.05, 'repeat_penalty': 1.05}
        with closing(self.runtime.stream_chat(messages, max_tokens=max_tokens, sampling=sampling)) as events:
            for event in events:
                if stop.is_set(): break
                text += event.content
                if len(text) > 20000: break
                if event.stop: break
        return text

    def _run_memory(self, chat_id, stop, force):
        from server.ego_core import memory as memory_module
        try:
            window = getattr(getattr(self.runtime, 'config', None), 'context', 8192)
            request = self.memory.extraction_request(chat_id, window=window, force=force)
            if request is not None and not stop.is_set():
                messages, source_from, source_to = request
                raw = self._collect(messages, memory_module.EXTRACT_MAX_TOKENS, stop)
                if not stop.is_set():
                    self.memory.apply_extraction(chat_id, raw, source_from, source_to)
            if not stop.is_set():
                summary = self.memory.summary_request(chat_id, counter=self._token_counter(self.runtime))
                if summary is not None:
                    messages, archived, through = summary
                    raw = self._collect(messages, memory_module.SUMMARY_MAX_TOKENS, stop)
                    if not stop.is_set():
                        self.memory.apply_summary(chat_id, raw, archived, through)
        except Exception as exc:
            if not stop.is_set():
                self.memory.mark_failed(chat_id, getattr(exc, 'code', None) or type(exc).__name__)
        finally:
            with self._condition:
                if self._active and self._active[0] == 'memory:' + chat_id:
                    if not self._closed and self._runtime_state == 'BUSY':
                        if hasattr(self.runtime, 'process') and self._pid() is None:
                            self.last_runtime_error = {'code': 'RUNTIME_EXITED'}
                            self._transition('ERROR', self.last_runtime_error)
                        else:
                            self._transition('READY')
                    self._active = None
                    self._start_next_locked()
                    self._condition.notify_all()

    # ---- 2026-11-06: «Enchant your prompt» — one card field, on request -------------------
    def enchant(self, kind, field, text, context, timeout=240):
        from server.ego_core import enchant as enchant_module
        messages = enchant_module.request(kind, field, text, context)      # CONTENT_BLOCKED / INVALID_INPUT first
        result = {'max_tokens': enchant_module.max_tokens(kind, field)}
        with self._condition:
            self._observed_crash()
            if self._closed: raise CoreFailure('CORE_STOPPING')
            if not isinstance(self.runtime, LlamaRuntime): raise CoreFailure('ENCHANT_UNAVAILABLE')
            if self._active or self._pending or self._loader is not None: raise CoreFailure('CORE_BUSY')
            if self._runtime_state != 'READY': raise CoreFailure('RUNTIME_NOT_READY')
            stop = threading.Event()
            key = 'enchant:' + uuid.uuid4().hex
            thread = threading.Thread(target=self._run_enchant, args=(key, messages, stop, result), daemon=True, name='ego-enchant')
            self._transition('BUSY')
            self._active = (key, stop, thread)
            thread.start()
        thread.join(timeout)
        if thread.is_alive():
            with self._condition:
                if self._active and self._active[0] == key:
                    stop.set()
                    try: self._cancel_generation(self._active)
                    except Exception: pass
            raise CoreFailure('CORE_BUSY')
        if stop.is_set(): raise CoreFailure('CORE_BUSY')              # a reply took the runtime
        if 'error' in result: raise CoreFailure(result['error'])
        return {'kind': kind, 'field': field, 'text': enchant_module.finish(kind, field, result.get('text'))}

    def _run_enchant(self, key, messages, stop, result):
        from server.ego_core import enchant as enchant_module
        try:
            result['text'] = self._collect(messages, result.pop('max_tokens', enchant_module.MAX_TOKENS), stop, enchant_module.SAMPLING)
        except Exception as exc:
            result['error'] = getattr(exc, 'code', None) or 'ENCHANT_UNAVAILABLE'
        finally:
            with self._condition:
                if self._active and self._active[0] == key:
                    if not self._closed and self._runtime_state == 'BUSY':
                        if hasattr(self.runtime, 'process') and self._pid() is None:
                            self.last_runtime_error = {'code': 'RUNTIME_EXITED'}
                            self._transition('ERROR', self.last_runtime_error)
                        else:
                            self._transition('READY')
                    self._active = None
                    self._start_next_locked()
                    self._condition.notify_all()

    def _open_stream(self, generation, identity, character_name, character_description, continuing):
        runtime=self.runtime
        if not isinstance(runtime, LlamaRuntime):
            # Legacy/injected plain-completion runtimes (tests, M1.3 adapters).
            return runtime.stream(self._prompt(generation['chat_id']))
        sampling = self._chat_sampling(generation['chat_id'])
        window, reserve = self._runtime_budget(runtime, sampling.get('max_tokens') if sampling else None)
        stored = self.store.continuation_context(identity) if continuing else self.store.generation_context(identity)
        counter = self._token_counter(runtime)
        # Conservative length estimate: no extra /tokenize round trips per fact before a reply.
        memory = self.memory.context(generation['chat_id'], window=window) if self.memory is not None else ''
        # The owner's name and form of address, only if they allowed it (profile.py).
        persona = self.profile.persona() if getattr(self, 'profile', None) is not None else ''
        memory = '\n\n'.join(part for part in (persona, memory) if part)
        messages = compile_messages(stored, character_name=character_name,
                                         character_description=character_description,
                                         context_window=window, response_reserve=reserve, memory=memory,
                                         preserve_recent_assistant=continuing,
                                         token_counter=counter)
        if not sampling:
            return runtime.stream_chat(messages)
        return runtime.stream_chat(messages, max_tokens=reserve, sampling=sampling)

    def _chat_sampling(self, chat_id):
        """Sampling of the chat's preset, or None to use the server profile."""
        getter = getattr(self.store, 'chat_settings', None)
        if getter is None:
            return None
        try:
            preset_id = getter(chat_id)['preset_id']
            if preset_id is None:
                return None
            from server.ego_core.presets import chat_sampling
            return chat_sampling(preset_id)
        except (StoreFailure, ValueError):
            return None  # Unknown/removed preset: fall back to the profile.

    @staticmethod
    def _token_counter(runtime):
        """Exact counts from the loaded model, or None to keep the byte heuristic."""
        count=getattr(runtime,'count_tokens',None)
        if count is None: return None
        try: count('EGO')
        except Exception: return None
        cache={}
        def counter(value):
            if value not in cache: cache[value]=count(value)
            return cache[value]
        return counter

    @staticmethod
    def _request_speed(output_tokens, elapsed):
        if type(output_tokens) is not int or output_tokens < 0 or type(elapsed) not in (int,float) or elapsed <= 0:
            return None
        return round(output_tokens / elapsed, 2)

    @staticmethod
    def _extract_timings(timings, tokens_predicted):
        """Accept only numeric counters; no prompts, completion text or raw logs."""
        import math
        def number(name):
            value = timings.get(name)
            return float(value) if type(value) in (float,int) and math.isfinite(value) and value >= 0 else None
        predicted = number('predicted_n')
        if predicted is None and type(tokens_predicted) is int and 0 <= tokens_predicted <= 1000000:
            predicted = tokens_predicted
        prompt = number('prompt_n')
        ms = number('predicted_ms')
        speed = number('predicted_per_second')
        if speed is None and predicted is not None and ms is not None and ms > 0:
            speed = predicted * 1000 / ms
        return {'tokens_per_second':round(speed,2) if speed is not None else None,
                'completion_tokens':int(predicted) if predicted is not None else None,
                'prompt_tokens':int(prompt) if prompt is not None else None,
                'generation_seconds':round(ms/1000,3) if ms is not None else None}

    def _cancel_generation(self, active):
        # Caller owns _condition and checks the exact generation identity. Never
        # cancel the next owner's stream after a delayed callback returns.
        method=getattr(self.runtime,'cancel_generation',None)
        if method is not None: method()
        elif self.runtime is not None:
            # Legacy injected runtimes may offer only a hard stop. Production
            # llama adapter implements soft cancellation and keeps model/PID.
            self.runtime.cancel()
            if self._runtime_state=='BUSY': self._transition('STOPPED')

    def stop(self, operation_id, generation_id):
        generation=self.store.request_stop(operation_id,generation_id)
        with self._condition:
            if generation['state']=='queued':
                for item in list(self._pending):
                    if item[0]['id']==generation_id:
                        self._pending.remove(item)
                        self.store.set_generation_state(generation_id,'stopped')
                        return self.store.generation(generation_id)
            active=self._active
            if active and active[0]==generation_id:
                active[1].set()
                self._condition.notify_all()
                try: self._cancel_generation(active)
                finally:
                    if generation_id not in self._stopping:
                        self._stopping.add(generation_id)
                        threading.Thread(target=self._continue_stop,args=(active,),daemon=True,name='ego-stop').start()
        return generation

    def _continue_stop(self, active):
        deadline=time.monotonic()+8
        try:
            while active[2].is_alive() and time.monotonic()<deadline:
                active[2].join(.05)
                with self._condition:
                    if not self._active or self._active[0]!=active[0]: return
                    self._cancel_generation(active)
            if active[2].is_alive():
                with self._condition:
                    if self._active and self._active[0]==active[0]:
                        self.last_runtime_error={'code':'GENERATION_CANCEL_TIMEOUT'}
                        self._transition('ERROR',self.last_runtime_error)
        finally:
            with self._condition: self._stopping.discard(active[0])

    def close(self):
        with self._close_lock:
            with self._condition:
                if self._closed and self._runtime_state=='STOPPED': return
                self._closed=True
                # Regeneration items are 3-tuples, continuation items 4-tuples.
                for pending, *_ in self._pending:
                    if self.store.generation(pending['id'])['state']=='queued':
                        self.store.set_generation_state(pending['id'],'stopped')
                self._pending.clear()
                self._transition('STOPPING')
                active,loader=self._active,self._loader
                if active: active[1].set()
                self._condition.notify_all()
            # No Core lock across bounded OS termination or joins. Prevent new
            # work before cancel, then cover adapter startup's private flag reset.
            deadline=time.monotonic()+8
            while True:
                runtime=self.runtime
                if runtime is not None: runtime.cancel()
                threads=[t for t in (active[2] if active else None,loader) if t is not None and t is not threading.current_thread()]
                for thread in threads: thread.join(.05)
                if not any(t.is_alive() for t in threads): break
                if time.monotonic()>=deadline: raise CoreFailure('SHUTDOWN_INCOMPLETE')
            if self.runtime is not None: self.runtime.shutdown()
            with self._condition:
                self._transition('STOPPED')
