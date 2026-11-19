"""Private background entrypoint; use the authenticated control client to stop."""
import argparse
import json
import sys
from pathlib import Path
from server.ego_core.control import ROOT, serve


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--startup-control',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:
        serve(args.root.absolute(),startup_control=sys.stdin if args.startup_control else None)
        return 0
    except BaseException as exc:
        code=getattr(exc,'code','CORE_SERVER_FAILED')
        # Exception messages, configuration, tokens and paths are never logged.
        result={'status':'FAIL','code':code}
        if isinstance(exc,OSError): result.update(errno=exc.errno,winerror=getattr(exc,'winerror',None))
        print(json.dumps(result),flush=True)
        return 2


if __name__=='__main__': raise SystemExit(main())
