"""Qt-independent model profiles and GUI preferences, separate from encrypted data."""
from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
from server.ego_core.config import CoreConfig, linked, load_config, save_config

_LOCK = threading.RLock()
_GUI = {'language':'ru', 'theme':'dark', 'reduced_effects':False, 'close_behavior':'ask'}


class ConfigurationFailure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json(path, limit=1024*1024):
    if linked(path) or linked(path.parent):
        raise ConfigurationFailure('CONFIG_PATH_UNSAFE')
    if not path.is_file() or path.stat().st_size > limit:
        raise ConfigurationFailure('CONFIG_INVALID')
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:
                raise ValueError('Duplicate field')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite number')))


def _write(path, value):
    if linked(path) or linked(path.parent):
        raise ConfigurationFailure('CONFIG_PATH_UNSAFE')
    temporary = None
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.settings-',suffix='.tmp',delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary,0o600)
            json.dump(value,file,ensure_ascii=False,allow_nan=False,indent=2)
            file.flush(); os.fsync(file.fileno())
        if _json(temporary) != value:
            raise ConfigurationFailure('CONFIG_INVALID')
        if linked(path) or linked(path.parent):
            raise ConfigurationFailure('CONFIG_PATH_UNSAFE')
        os.replace(temporary,path)
        temporary = None
    except OSError as exc:
        raise ConfigurationFailure('CONFIG_WRITE_FAILED') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _config(values, code='PROFILE_INVALID'):
    try:
        if not isinstance(values,dict):
            raise ValueError()
        normalized = dict(values)
        for key in ('binary','model'):
            value = normalized.get(key)
            if isinstance(value,str):
                value = value.strip()
                if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):
                    value = value[1:-1]
                normalized[key] = value
        return CoreConfig.model_validate(normalized)
    except (ValueError,TypeError) as exc:
        raise ConfigurationFailure(code) from exc


def _gui(values):
    if not isinstance(values,dict) or set(values)-set(_GUI):
        raise ConfigurationFailure('GUI_INVALID')
    result = {**_GUI,**values}
    if (result['language'] not in ('ru','en','fr','es','de') or result['theme'] not in ('dark','light')
            or type(result['reduced_effects']) is not bool
            or result['close_behavior'] not in ('ask','hide','stop')):
        raise ConfigurationFailure('GUI_INVALID')
    return result


class ConfigurationManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.core_path = self.root/'data/config.json'
        self.registry_path = self.root/'settings/profiles.json'
        self.gui_path = self.root/'settings/gui.json'
        with _LOCK:
            if not self.registry_path.exists() and not linked(self.registry_path):
                registry = self._empty()
                try:
                    # Import only an existing valid legacy schema. Missing external
                    # files remain editable and are checked at apply time.
                    if self.core_path.exists():
                        config = load_config(self.core_path)
                        profile = {'id':uuid.uuid4().hex,'name':'Imported configuration',
                                   'values':config.model_dump()}
                        registry['profiles'] = [profile]
                        registry['selected_id'] = profile['id']
                    _write(self.registry_path,registry)
                except (ValueError,OSError):
                    # Snapshot reports the actual error; never replace bad core data.
                    pass

    @staticmethod
    def _empty():
        return {'version':1,'profiles':[],'selected_id':None,'working':None}

    def _registry(self):
        if not self.registry_path.exists():
            if linked(self.registry_path) or linked(self.registry_path.parent):
                raise ConfigurationFailure('CONFIG_PATH_UNSAFE')
            return self._empty()
        try:
            obj = _json(self.registry_path)
            if not isinstance(obj,dict) or set(obj)!={'version','profiles','selected_id','working'} or type(obj['version']) is not int or obj['version']!=1:
                raise ValueError()
            if not isinstance(obj['profiles'],list) or len(obj['profiles'])>100:
                raise ValueError()
            seen = set()
            for profile in obj['profiles']:
                self._validate_profile(profile)
                if profile['id'] in seen:
                    raise ValueError()
                seen.add(profile['id'])
            if obj['selected_id'] is not None and obj['selected_id'] not in seen:
                raise ValueError()
            if obj['working'] is not None:
                working = obj['working']
                if not isinstance(working,dict) or set(working)!={'profile_id','values'}:
                    raise ValueError()
                if working['profile_id'] is not None and not isinstance(working['profile_id'],str):
                    raise ValueError()
                _config(working['values'])
            return obj
        except (ValueError,TypeError,OSError) as exc:
            if isinstance(exc,ConfigurationFailure) and exc.code=='CONFIG_PATH_UNSAFE':
                raise
            raise ConfigurationFailure('PROFILE_REGISTRY_INVALID') from exc

    @staticmethod
    def _validate_profile(profile):
        if (not isinstance(profile,dict) or set(profile)!={'id','name','values'}
                or not isinstance(profile['id'],str) or not 1<=len(profile['id'])<=64
                or not isinstance(profile['name'],str) or not 1<=len(profile['name'].strip())<=128):
            raise ConfigurationFailure('PROFILE_INVALID')
        _config(profile['values'])

    @staticmethod
    def _find(registry, profile_id):
        for profile in registry['profiles']:
            if profile['id']==profile_id:
                return profile
        raise ConfigurationFailure('PROFILE_NOT_FOUND')

    def snapshot(self):
        with _LOCK:
            error = None
            try:
                registry = self._registry()
            except ConfigurationFailure as exc:
                registry = self._empty(); error = exc.code
            try:
                gui = _gui(_json(self.gui_path)) if self.gui_path.exists() else dict(_GUI)
            except (ValueError,OSError):
                gui = dict(_GUI); error = error or 'GUI_INVALID'
            try:
                core = load_config(self.core_path).model_dump()
            except (ValueError,OSError):
                core = CoreConfig().model_dump()
                # Preserve allowlisted candidate values for repair in the UI.
                try:
                    raw = _json(self.core_path,16384)
                    if isinstance(raw,dict):
                        core.update({k:v for k,v in raw.items() if k in CoreConfig.model_fields})
                except (ValueError,OSError):
                    pass
                error = error or 'CONFIG_INVALID'
            return deepcopy({'profiles':registry['profiles'],'selected_id':registry['selected_id'],
                             'gui':gui,'core':core,'last_working':registry['working'] is not None,
                             'error':error})

    def save_profile(self,name,values,profile_id=None):
        with _LOCK:
            if not isinstance(name,str) or not 1<=len(name.strip())<=128:
                raise ConfigurationFailure('PROFILE_INVALID')
            config = _config(values)
            registry = self._registry()
            profile = {'id':profile_id or uuid.uuid4().hex,'name':name.strip(),'values':config.model_dump()}
            self._validate_profile(profile)
            if profile_id is None:
                if len(registry['profiles'])>=100:
                    raise ConfigurationFailure('PROFILE_LIMIT')
                registry['profiles'].append(profile)
            else:
                old = self._find(registry,profile_id)
                registry['profiles'][registry['profiles'].index(old)] = profile
            _write(self.registry_path,registry)
            return deepcopy(profile)

    def remove_profile(self,profile_id):
        with _LOCK:
            registry = self._registry(); profile = self._find(registry,profile_id)
            registry['profiles'].remove(profile)
            if registry['selected_id']==profile_id:
                registry['selected_id'] = None
            _write(self.registry_path,registry)

    def select_profile(self,profile_id):
        with _LOCK:
            registry = self._registry(); profile = self._find(registry,profile_id)
            registry['selected_id'] = profile_id
            _write(self.registry_path,registry)
            return deepcopy(profile)

    def _apply(self,config):
        try:
            config.validate_files()
            if linked(self.core_path) or linked(self.core_path.parent):
                raise ConfigurationFailure('CONFIG_PATH_UNSAFE')
            self.core_path.parent.mkdir(parents=True,exist_ok=True)
            save_config(self.core_path,config)
            return config.model_dump()
        except ConfigurationFailure:
            raise
        except ValueError as exc:
            code = str(exc)
            raise ConfigurationFailure(code if code in {'RUNTIME_NOT_PROVIDED','MODEL_NOT_PROVIDED','INVALID_GGUF','CONFIG_PATH_UNSAFE'} else 'CONFIG_INVALID') from exc
        except OSError as exc:
            raise ConfigurationFailure('CONFIG_WRITE_FAILED') from exc

    def apply_profile(self,profile_id):
        with _LOCK:
            registry = self._registry(); profile = self._find(registry,profile_id)
            return self._apply(_config(profile['values']))

    def mark_working(self,profile_id=None):
        with _LOCK:
            registry = self._registry()
            if profile_id is not None:
                self._find(registry,profile_id)
            try:
                config = load_config(self.core_path)
                config.validate_files()
            except (ValueError,OSError) as exc:
                raise ConfigurationFailure('CONFIG_INVALID') from exc
            registry['working'] = {'profile_id':profile_id,'values':config.model_dump()}
            _write(self.registry_path,registry)

    def working_config(self):
        """Return detached validated values for preflight before stopping Core."""
        with _LOCK:
            working = self._registry()['working']
            return None if working is None else _config(working['values']).model_dump()

    def restore_working(self):
        with _LOCK:
            working = self._registry()['working']
            if working is None:
                raise ConfigurationFailure('NO_WORKING_CONFIGURATION')
            return self._apply(_config(working['values']))

    def save_gui(self,values):
        with _LOCK:
            current = _gui(_json(self.gui_path)) if self.gui_path.exists() else dict(_GUI)
            _gui(values)
            result = _gui({**current,**values})
            _write(self.gui_path,result)
            return result

    def export_profile(self,profile_id,path):
        with _LOCK:
            destination = Path(path).absolute()
            if any(linked(p) for p in (destination,*destination.parents)):
                raise ConfigurationFailure('PROFILE_EXPORT_PATH_UNSAFE')
            destination = destination.resolve()
            if (destination.suffix.lower() != '.json' or
                any((self.root/name) in (destination,*destination.parents)
                    for name in ('data','settings','.venv','.git','models'))):
                raise ConfigurationFailure('PROFILE_EXPORT_PATH_UNSAFE')
            profile = self._find(self._registry(),profile_id)
            _write(destination,{'version':1,'name':profile['name'],
                               'values':_config(profile['values']).model_dump()})

    def import_profile(self,path):
        with _LOCK:
            try:
                obj = _json(Path(path))
                if not isinstance(obj,dict) or set(obj)!={'version','name','values'} or type(obj['version']) is not int or obj['version']!=1:
                    raise ValueError()
                return self.save_profile(obj['name'],obj['values'])
            except (ValueError,OSError,KeyError) as exc:
                raise ConfigurationFailure('PROFILE_INVALID') from exc
