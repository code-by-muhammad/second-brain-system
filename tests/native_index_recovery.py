"""Exercise index convergence on the installed service in the disposable setup job."""

import json
import os
from pathlib import Path
import subprocess
import time


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("This test requires the disposable native setup job.")
    scratch = Path(os.environ["RUNNER_TEMP"]).resolve()
    root = Path(os.environ["SECOND_BRAIN_ROOT"]).resolve()
    vault = (scratch / "second brain notes").resolve()
    if root != scratch / "second-brain-runtime" or vault != scratch / "second brain notes":
        raise SystemExit("The runtime and vault must be under the native runner scratch directory.")
    device = json.loads((root / "device.json").read_text(encoding="utf-8"))
    if Path(device["vault"]).resolve() != vault:
        raise SystemExit("The installed service does not belong to the disposable vault.")

    cli = [device["python"], "-B", str(root / "app/brain_cli.py")]
    if Path(cli[0]).name.lower() == "pythonw.exe":
        cli[0] = str(Path(cli[0]).with_name("python.exe"))

    def call(*args, timeout=120):
        output = subprocess.run(
            cli + list(args), check=True, capture_output=True, encoding="utf-8",
            timeout=timeout,
        ).stdout
        return json.loads(output)

    baseline = call("index", "status")
    assert baseline["status"] == "verified", baseline
    assert call("service", "stop", "--wait-seconds", "60", timeout=90)["status"] == "stopped"

    edited = vault / "Maps/Start.md"
    former = vault / "Templates/project-brief.md"
    moved = former.with_name("native-index-renamed-brief.md")
    large = vault / "native-index-large.md"
    stat = edited.stat()
    original = edited.read_bytes()
    edited_bytes = original.replace(b"Capture something", b"zirconiumcanary!!", 1)
    assert edited_bytes != original and len(edited_bytes) == len(original)
    moved_bytes = former.read_bytes()
    large_bytes = ("# Native index large fixture\n\n" + "".join(
        f"- Quartz shard {number:03d} contains one synthetic recovery fact.\n"
        for number in range(265)
    )).encode("utf-8")
    edited.write_bytes(edited_bytes)
    os.utime(edited, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert edited.stat().st_mtime_ns == stat.st_mtime_ns
    former.rename(moved)
    large.write_bytes(large_bytes)

    drift = call("index", "status")
    assert drift["status"] == "needs review", drift
    assert all(drift["coverage"][key] > 0 for key in ("missing", "stale", "extra")), drift
    assert call("service", "start", timeout=200)["status"] == "started"

    deadline = time.monotonic() + 480
    current = drift
    while (remaining := deadline - time.monotonic()) > 0:
        current = call("index", "status", timeout=min(120, remaining))
        if current["status"] == "verified":
            break
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    assert current["status"] == "verified", current
    assert current["inventory_stable"], current
    assert current["coverage"]["manifest_sha256"] == drift["coverage"]["manifest_sha256"], current
    assert current["coverage"]["eligible"] == baseline["coverage"]["eligible"] + 1, current
    assert current["vectors"]["chunks"] - baseline["vectors"]["chunks"] > 256, current
    assert not former.exists()
    assert edited.read_bytes() == edited_bytes
    assert moved.read_bytes() == moved_bytes
    assert large.read_bytes() == large_bytes

    search = call("search", "zirconiumcanary", "--limit", "15")
    assert any(
        hit["path"] == "Maps/Start.md" and "zirconiumcanary" in hit["snippet"]
        for branch in search["indexed"] for hit in branch["results"]
    ), search["indexed"]
    print("PASS   native index recovered same-size/mtime edit, rename and 260+ new chunks")


if __name__ == "__main__":
    main()
