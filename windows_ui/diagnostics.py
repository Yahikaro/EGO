"""Bounded diagnostic projections of EGO-owned reports, never raw file copies."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
import zipfile

from server.ego_core.config import linked
from tools.windows.runner import core_diagnostic_fields

STAMP=r'\d{8}-\d{6}-\d{6}'
from server.ego_core.logrotate import OWNED  # shared with automatic rotation (stage 4e)
LATEST={'latest-test-report.txt','latest-test-report.xml','latest-benchmark-report.json','latest-core-report.json','latest-core-api-error.json','latest-diagnostics.json'}
ROTATED={'core-api-errors.log','core-api-errors.log.1','core-api-errors.log.2','gui.log','gui-bootstrap.log'}
CODES=re.compile(r'[A-Z][A-Z0-9_]{0,63}')
NUMBERS={'passed','failed','skipped','errno','winerror','exit_code','duration_seconds','output_characters','tokens_predicted','context','input_tokens','ttft_seconds','elapsed_seconds','initial_worker_load_seconds','restart_after_cancel_load_seconds','sampled_peak_rss_bytes','sampled_peak_gpu_all_devices_used_mib','rss_samples','model_size_bytes','recovered_generations'}
NUMBERS.update({'tokens_per_second','generation_seconds','completion_tokens','prompt_tokens','runtime_pid','model_load_seconds'})
ENUMS={'status':{'PASS','FAIL','BLOCKED','CANCELLED','SKIPPED'},'runtime':{'unconfigured','idle','busy'},'storage':{'ready'},'gpu_execution':{'NOT_VERIFIED'},'action':{'initialize','start','stop','restart','environment','smoke','core_tests','runtime_tests','security_tests','full_tests','fixture_tests','benchmark','gpu_verify','diagnostics','apply_profile','restore_working','unknown'}}
ENUMS['runtime_state']={'STOPPED','STARTING','LOADING','READY','BUSY','STOPPING','ERROR'}
ENUMS['scope']={'warm_runtime_generation','fixture_contract_checks_only'}
ENUMS['action'].update({'auto_start','runtime_restart'})


def _safe(path):
    return not any(linked(p) for p in (path,*path.parents))


def _projection(value,depth=0):
    if not isinstance(value,dict) or depth>3: return {}
    selected={}
    for key,options in ENUMS.items():
        item=value.get(key)
        if isinstance(item,str) and item in options: selected[key]=item
    for key in ('code','reason','cause_code'):
        item=value.get(key)
        if isinstance(item,str) and CODES.fullmatch(item): selected[key]=item
    for key in NUMBERS:
        item=value.get(key)
        if type(item) in (int,float) and -2**31<=item<=2**63: selected[key]=item
    for key in ('core','core_status','last_runtime_error','os_error','first','repeated','after_cancel','after_crash','runtime_diagnostics'):
        item=_projection(value.get(key),depth+1)
        if item: selected[key]=item
    rows=value.get('measurements')
    if isinstance(rows,list): selected['measurements']=[_projection(row,depth+1) for row in rows[:4] if isinstance(row,dict)]
    # Core's helper independently validates a bounded set of operational fields.
    selected.update(core_diagnostic_fields(value))
    return selected


def _text_projection(text):
    fields={}
    for line in text.splitlines()[:300]:
        match=re.fullmatch(r'(Status|Passed|Failed|Skipped|Exit code|Duration):\s*(.*?)\s*',line)
        if not match: continue
        key,value=match.groups()
        if key=='Status' and value in ENUMS['status']: fields['status']=value
        elif key in {'Passed','Failed','Skipped','Exit code'} and re.fullmatch(r'-?\d{1,9}',value): fields[key.lower().replace(' ','_')]=int(value)
        elif key=='Duration' and re.fullmatch(r'\d{1,8}(?:\.\d{1,6})?s',value): fields['duration_seconds']=float(value[:-1])
    return fields


def _files(root):
    directory=Path(root)/'logs'
    if not _safe(directory) or not directory.is_dir(): return []
    return sorted((p for p in directory.iterdir() if (OWNED.fullmatch(p.name) or p.name in LATEST|ROTATED) and _safe(p) and p.is_file()),key=lambda p:p.name,reverse=True)[:60]


def logs(root,level='',search=''):
    rows=[]
    level=level.upper()
    search=str(search)[:256].casefold()
    for path in _files(root):
        try:
            size=path.stat().st_size
            # JSON is parsed whole or omitted; XML gets no raw text extraction.
            if size>131072 or path.suffix=='.xml': continue
            with path.open('rb') as stream: raw=stream.read(131073)
            if len(raw)>131072: continue
            text=raw.decode('utf-8',errors='replace')
            values=[]
            if path.suffix=='.json':
                try: values=[_projection(json.loads(text))]
                except ValueError: continue
            elif path.name=='latest-test-report.txt' or OWNED.fullmatch(path.name):
                values=[_text_projection(text)]
                # Core child startup reports are a single structured JSON object.
                try: values.append(_projection(json.loads(text)))
                except ValueError: pass
            else:
                for line in text.splitlines()[-200:]:
                    try: values.append(_projection(json.loads(line)))
                    except ValueError: pass
            for value in values:
                if not value: continue
                row_level='ERROR' if value.get('status')=='FAIL' else ('WARNING' if value.get('status') in {'BLOCKED','CANCELLED'} else 'INFO')
                encoded=json.dumps(value,ensure_ascii=True,sort_keys=True)
                if level and level!=row_level: continue
                if search and search not in encoded.casefold(): continue
                rows.append({'file':path.name,'level':row_level,'text':encoded})
        except OSError: continue
    return rows[-1000:]


def export_diagnostics(root,destination):
    root=Path(root); destination=Path(destination).absolute()
    if not _safe(root/'logs') or not _safe(destination) or destination.suffix.lower()!='.zip': raise ValueError('DIAGNOSTIC_PATH_UNSAFE')
    destination=destination.resolve()
    if any((root/name).absolute() in destination.parents for name in ('data','settings','models','model','user','users','.venv','.git')): raise ValueError('DIAGNOSTIC_PATH_UNSAFE')
    destination.parent.mkdir(parents=True,exist_ok=True)
    rows=logs(root)
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'Sanitized operational metadata only; no raw logs, configuration, conversations, keys or user files.','entries':len(rows),'native_windows_acceptance':'PENDING'}
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.ego-diagnostics-',suffix='.zip',delete=False) as stream: temporary=Path(stream.name)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',json.dumps(manifest,indent=2))
            archive.writestr('operational-reports.json',json.dumps(rows,ensure_ascii=True,indent=2))
        os.replace(temporary,destination); temporary=None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return destination


def clean_logs(root,*,days,confirmed):
    if type(days) is not int or not 0<=days<=36500: raise ValueError('LOG_RETENTION_INVALID')
    if not confirmed: return 0
    count=0; cutoff=time.time()-days*86400
    for path in _files(root):
        # Latest evidence and active/rotating logs are never removed.
        if not OWNED.fullmatch(path.name): continue
        try:
            if path.stat().st_mtime<cutoff: path.unlink(); count+=1
        except OSError: continue
    return count
