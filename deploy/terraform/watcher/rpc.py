"""JSON dispatcher for the recovery operations, reached through a forced SSH command on the AWS VM.

Reads one request {"action": ..., "payload": {...}} from stdin, prints one {"ok": ..., ...} line.
Only names listed in recovery_ops.ACTIONS can be called.
"""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery_ops


def main():
    os.umask(0o077)
    try:
        request = json.load(sys.stdin)
        action = recovery_ops.ACTIONS[request['action']]
        print(json.dumps({'ok': True, 'result': action(request.get('payload') or {})}))
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else f'{type(exc).__name__}: operation failed'
        print(json.dumps({'ok': False, 'error': message}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
