"""Relay pytest output from a file so descendants cannot hold the CI pipe."""

import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    directory, *args = sys.argv[1:]
    directory = Path(directory)
    path = directory / 'pytest-output.log'
    with path.open('wb') as log, path.open('rb', buffering=0) as output:
        pytest = subprocess.Popen(
            [sys.executable, '-B', '-m', 'pytest', *args],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
        while True:
            code = pytest.poll()
            # At exit, relay only the bytes already present even if a child
            # continues writing to the log.
            remaining = os.fstat(output.fileno()).st_size - output.tell()
            while remaining > 0:
                data = output.read(min(remaining, 64 * 1024))
                if not data:
                    break
                sys.stdout.buffer.write(data)
                remaining -= len(data)
            sys.stdout.buffer.flush()
            if code is not None:
                break
            time.sleep(0.5)
    (directory / 'exit-code.txt').write_text(f'{code}\n', encoding='ascii')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
