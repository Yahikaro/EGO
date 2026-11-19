"""Bounded, read-only host observations. No process ownership inference."""
from __future__ import annotations
from pathlib import Path
import csv
import json
import math
import os
import subprocess

import psutil
from server.ego_core.config import linked


def _number(value,low=0,high=1e12):
    try:
        value=float(value)
        return value if math.isfinite(value) and low<=value<=high else None
    except (TypeError,ValueError): return None


def snapshot(root):
    result={key:None for key in ('cpu_percent','ram_used_bytes','ram_total_bytes','ram_percent','gpu_percent','vram_used_mib','vram_total_mib','gpu_temperature_c','runtime_listener','model_load_seconds','tokens_per_second','context_used')}
    result.update(errors=[],gpus=[],gpu_scope='whole device including other applications',runtime_process='UNKNOWN', runtime_memory_mib=None, runtime_uptime_seconds=None, runtime_executable=None)
    try:
        memory=psutil.virtual_memory()
        result.update(cpu_percent=psutil.cpu_percent(interval=.1),ram_used_bytes=memory.used,ram_total_bytes=memory.total,ram_percent=memory.percent)
    except (psutil.Error,OSError): result['errors'].append('SYSTEM_METRICS_UNAVAILABLE')
    try:
        output=subprocess.run(['nvidia-smi','--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        if output.returncode: result['errors'].append('NVIDIA_SMI_FAILED')
        elif len(output.stdout)>16384: result['errors'].append('NVIDIA_SMI_INVALID_DATA')
        else:
            for row in list(csv.reader(output.stdout.splitlines()))[:16]:
                if len(row)!=5:
                    result['errors'].append('NVIDIA_SMI_INVALID_DATA'); continue
                gpu=dict(zip(('index','gpu_percent','vram_used_mib','vram_total_mib','gpu_temperature_c'),(_number(v.strip()) for v in row)))
                if gpu['gpu_percent'] is not None and gpu['gpu_percent']>100: gpu['gpu_percent']=None
                if any(v is None for v in gpu.values()): result['errors'].append('NVIDIA_SMI_INVALID_DATA')
                result['gpus'].append(gpu)
            if result['gpus']:
                first=result['gpus'][0]
                result.update({key:first[key] for key in ('gpu_percent','vram_used_mib','vram_total_mib','gpu_temperature_c')})
    except FileNotFoundError: result['errors'].append('NVIDIA_SMI_UNAVAILABLE')
    except subprocess.TimeoutExpired: result['errors'].append('NVIDIA_SMI_TIMEOUT')
    except (OSError,ValueError): result['errors'].append('NVIDIA_SMI_FAILED')
    port=18081
    path=Path(root)/'data/config.json'
    try:
        if not linked(path) and not linked(path.parent) and path.is_file() and path.stat().st_size<=16384:
            config=json.loads(path.read_bytes())
            if type(config.get('runtime_port')) is int and 1024<=config['runtime_port']<=65535: port=config['runtime_port']
    except (OSError,ValueError,AttributeError): result['errors'].append('CONFIG_MONITOR_UNAVAILABLE')
    try:
        for connection in psutil.net_connections(kind='tcp'):
            if connection.status==psutil.CONN_LISTEN and connection.laddr and connection.laddr.port==port and connection.laddr.ip=='127.0.0.1':
                result['runtime_listener']={'pid':connection.pid,'port':port,'observed':True,'owned':False}
                result['runtime_process']='LISTENER_OBSERVED'
                # Observing a loopback listener is not permission to manage it.
                # Never expose command lines: they may include private paths or keys.
                if type(connection.pid) is int and connection.pid>0:
                    try:
                        proc=psutil.Process(connection.pid)
                        name=proc.name().lower()
                        if name in {'llama-server.exe','llama-server'}:
                            result['runtime_process']='LLAMA_SERVER_OBSERVED'
                            result['runtime_memory_mib']=round(proc.memory_info().rss/1048576,1)
                            result['runtime_uptime_seconds']=round(max(0,__import__('time').time()-proc.create_time()),1)
                            result['runtime_executable']='llama-server.exe'
                    except (psutil.Error,OSError,ValueError):
                        result['errors'].append('RUNTIME_PROCESS_METRICS_UNAVAILABLE')
                break
    except (psutil.Error,OSError): result['errors'].append('LISTENER_INSPECTION_UNAVAILABLE')
    result['errors']=list(dict.fromkeys(result['errors']))
    return result
