"""Optional voice output: create the TTS environment next to EGO and download the XTTS-v2 model.

    py -3.12 scripts\\install_tts.py            (Windows, Python 3.12)

Creates tts\\.venv, installs torch + coqui-tts (several GB), downloads the model into tts\\models.
Needs an NVIDIA GPU for fast speech (CPU works, but slowly). Safe to run again.
XTTS-v2 weights: Coqui Public Model License, non-commercial use only.
"""
from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

HOME = Path(__file__).resolve().parents[1] / 'tts'
PY = HOME / '.venv' / 'Scripts' / 'python.exe'


def run(*args):
    print('>', ' '.join(str(a) for a in args))
    subprocess.run([str(a) for a in args], check=True)


def main() -> int:
    HOME.mkdir(exist_ok=True)
    if not PY.exists():
        venv.EnvBuilder(with_pip=True).create(HOME / '.venv')
    run(PY, '-m', 'pip', 'install', '--upgrade', 'pip')
    run(PY, '-m', 'pip', 'install', 'torch==2.5.1', 'torchaudio==2.5.1', '--index-url', 'https://download.pytorch.org/whl/cu124')
    run(PY, '-m', 'pip', 'install', 'coqui-tts', 'soundfile', 'transformers<5', 'datasets<4', 'huggingface_hub')
    run(PY, '-m', 'pip', 'uninstall', '-y', 'torchcodec')
    code = ("import os;from pathlib import Path;from huggingface_hub import hf_hub_download;"
            f"root=Path(r'{HOME}');os.environ.setdefault('HF_HOME',str(root/'hf'));"
            "[hf_hub_download('coqui/XTTS-v2',f,local_dir=root/'models'/'xtts_v2') for f in "
            "('config.json','vocab.json','speakers_xtts.pth','model.pth')]")
    run(PY, '-c', code)
    print('Done. Restart EGO Core; add a voice in the app (Voices window).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
