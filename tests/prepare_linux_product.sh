#!/usr/bin/env bash
# ONLY a fresh disposable GitHub Ubuntu VM; no production execution.
set -euo pipefail
set -o noclobber
umask 022
[[ $# == 10 && "$EUID" -ne 0 ]]
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]
[[ "${RUNNER_OS:-}" == Linux && "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]]
candidate_source=$1;fixture_source=$2;uv_source=$3;mode=$4;tag=$5;commit=$6;manifest_sha=$7;harness_sha=$8;workflow_sha=$9;report_directory=${10}
[[ "$mode" =~ ^(forward|rollback|recovery)$ && "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
[[ "$commit" =~ ^[a-f0-9]{40}$ && "$manifest_sha" =~ ^[a-f0-9]{64}$ && "$harness_sha" =~ ^[a-f0-9]{64}$ ]]
[[ "$workflow_sha" =~ ^[a-f0-9]{40}$ && "${GITHUB_WORKFLOW_SHA:-}" == "$workflow_sha" ]]
[[ "$candidate_source" == /* && "$fixture_source" == /* && "$uv_source" == /* && "$report_directory" == /* ]]
[[ "$(/usr/bin/git -C "$candidate_source" rev-parse HEAD)" == "$commit" ]]
[[ "$(/usr/bin/sha256sum "$candidate_source/release-manifest.json" | /usr/bin/cut -d' ' -f1)" == "$manifest_sha" ]]
[[ "$(/usr/bin/sha256sum "$fixture_source" | /usr/bin/cut -d' ' -f1)" == "$harness_sha" ]]
uv_sha=$(/usr/bin/sha256sum "$uv_source" | /usr/bin/cut -d' ' -f1)
[[ "$uv_sha" =~ ^[a-f0-9]{64}$ ]]
qualification_base="/var/lib/second-brain-product-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${mode}"
qualification_source="$qualification_base/software"
case "$mode" in forward) mode_letter=f ;; rollback) mode_letter=b ;; recovery) mode_letter=c ;; esac
qualification_user="sbci${GITHUB_RUN_ID}${GITHUB_RUN_ATTEMPT}${mode_letter}"
qualification_receipt="$report_directory/linux-product-qualification.json"
[[ ! -e "$qualification_base" && ! -e "$qualification_receipt" ]]
if /usr/bin/id "$qualification_user" >/dev/null 2>&1; then echo 'Fresh fixture account required; refusing reuse.' >&2; exit 1; fi
mkdir -p "$report_directory"
wrapper_stage='protected SDK preparation'
wrapper_receipt() {
  wrapper_exit=$?
  if [[ ! -e "$qualification_receipt" ]]; then
    printf '{"status":"needs review","scope":"Wrapper preflight only; no native acceptance claim","stage":"%s","exit_code":%s,"mode":"%s","candidate_commit":"%s"}\n' \
      "$wrapper_stage" "$wrapper_exit" "$mode" "$commit" > "$qualification_receipt"
  fi
}
trap wrapper_receipt EXIT

# Observed hosted /opt is writable. Never modify shared ancestors.
qualification_validator='
import sys
from pathlib import Path
def check(paths):
    for selected in paths:
        if not selected.is_absolute() or any(p.is_symlink() for p in (selected,*selected.parents)):
            raise RuntimeError("Linked or relative qualification protection boundary: "+str(selected))
        for part in (selected,*selected.parents):
            info=part.stat()
            if info.st_uid!=0 or info.st_mode&0o022:
                raise RuntimeError("Unprotected qualification ancestor: "+str(part)+" uid="+str(info.st_uid)+" mode="+oct(info.st_mode&0o7777))
if sys.argv[1:2]==["--parent-sdk"]:
    if len(sys.argv)!=5:raise RuntimeError("Exact protected SDK paths required")
    paths=[Path(sys.argv[2]),Path(sys.prefix),Path(sys._base_executable).resolve(),Path(sys.argv[3]).resolve(),Path(sys.argv[4])]
else:
    if len(sys.argv)!=1:raise RuntimeError("Fixed protection ancestor required")
    paths=[Path("/var/lib")]
check(paths)
print("Protected qualification ancestry verified.")
'
# Fail before any privileged SDK creation if the proposed ancestor is unsuitable.
/usr/bin/python3.12 -I -B -c "$qualification_validator"

# Read-only parent SDK, driver and tools are outside the runner's private home.
# Exact safe.directory is only needed for the reviewed runner-owned source.
sudo /usr/bin/install -d -m 0755 "$qualification_base"
sudo /usr/bin/install -m 0555 "$uv_source" "$qualification_base/uv"
sudo /usr/bin/install -m 0444 "$fixture_source" "$qualification_base/native_linux_upgrade.py"
[[ "$(sudo /usr/bin/sha256sum "$qualification_base/uv" | /usr/bin/cut -d' ' -f1)" == "$uv_sha" ]]
[[ "$(sudo /usr/bin/sha256sum "$qualification_base/native_linux_upgrade.py" | /usr/bin/cut -d' ' -f1)" == "$harness_sha" ]]
sudo /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
  /usr/bin/git --no-replace-objects --no-optional-locks -c "safe.directory=$candidate_source" -c "safe.directory=$candidate_source/.git" -c core.fsmonitor=false -c core.hooksPath=/dev/null \
  clone --no-hardlinks --no-checkout --template= --config core.autocrlf=false -- "$candidate_source" "$qualification_source"
sudo /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null /usr/bin/git -C "$qualification_source" remote set-url origin https://github.com/code-by-muhammad/second-brain-system.git
sudo /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null /usr/bin/git -C "$qualification_source" -c core.hooksPath=/dev/null checkout --detach "$tag"
[[ "$(sudo /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null /usr/bin/git -C "$qualification_source" rev-parse HEAD)" == "$commit" ]]
sudo /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin UV_NO_CONFIG=1 UV_CACHE_DIR="$qualification_base/uv-cache" UV_PYTHON_INSTALL_DIR="$qualification_base/python" \
  "$qualification_base/uv" sync --locked --no-dev --project "$qualification_source" --python 3.12
# All bytes here are public pinned source/tools/packages, with no credentials.
sudo /usr/bin/chmod -R u=rwX,go=rX "$qualification_base"
qualification_python="$qualification_source/.venv/bin/python"
# Repeat the actual harness protection checks BEFORE account/linger/bus work.
/usr/bin/env -i HOME=/nonexistent PATH=/usr/bin:/bin "$qualification_python" -I -B -c "$qualification_validator" \
  --parent-sdk "$qualification_source" "$qualification_base/native_linux_upgrade.py" "$qualification_base/uv"

# Actual architecture/service/update admission BEFORE account, linger or bus work.
/usr/bin/env -i HOME=/nonexistent PATH=/usr/bin:/bin "$qualification_python" -I -B -c \
  'import sys;sys.path.insert(0,sys.argv[1]);import update;update._candidate_platform();update._qualified_platform();sys.path.insert(0,sys.argv[1]+"/app");import brain_service as s;s._qualified_native_platform()' "$qualification_source"

wrapper_stage='fresh account and populated manager bus'
sudo /usr/sbin/useradd --create-home --shell /bin/bash "$qualification_user"
qualification_uid=$(/usr/bin/id -u "$qualification_user")
qualification_home="/home/$qualification_user"
[[ "$qualification_uid" -ge 1000 && "$(/usr/bin/getent passwd "$qualification_user" | /usr/bin/cut -d: -f6)" == "$qualification_home" ]]
sudo /usr/bin/loginctl enable-linger "$qualification_user"
sudo /usr/bin/systemctl start "user@$qualification_uid.service"
# Non-login privilege drop: no PAM login session is created by the fixture.
sudo /usr/sbin/runuser -u "$qualification_user" -- /usr/bin/env -i HOME="$qualification_home" USER="$qualification_user" LOGNAME="$qualification_user" \
  PATH=/usr/bin:/bin XDG_RUNTIME_DIR="/run/user/$qualification_uid" /usr/bin/systemctl --user start dbus.service
bus_events="/sys/fs/cgroup/user.slice/user-${qualification_uid}.slice/user@${qualification_uid}.service/session.slice/dbus.service/cgroup.events"
[[ -f "$bus_events" ]]
/usr/bin/grep -qx 'populated 1' "$bus_events"

set +e
wrapper_stage='actual product fixture'
sudo /usr/sbin/runuser -u "$qualification_user" -- /bin/bash -c 'umask 022; cd / && exec "$@"' sbci /usr/bin/env -i HOME="$qualification_home" USER="$qualification_user" LOGNAME="$qualification_user" \
  PATH=/usr/bin:/bin XDG_RUNTIME_DIR="/run/user/$qualification_uid" GITHUB_ACTIONS=true GITHUB_SHA="$GITHUB_SHA" \
  GITHUB_RUN_ID="$GITHUB_RUN_ID" GITHUB_WORKFLOW_SHA="$workflow_sha" RUNNER_ENVIRONMENT=github-hosted \
  "$qualification_python" -I -B "$qualification_base/native_linux_upgrade.py" --execute-reviewed-native \
  --source-repository "$qualification_source" --candidate-tag "$tag" --candidate-commit "$commit" --candidate-manifest-sha256 "$manifest_sha" \
  --reviewed-harness-sha256 "$harness_sha" --workflow-sha "$workflow_sha" --expected-arch x86_64 --expected-ubuntu-version 24.04 \
  --expected-user "$qualification_user" --mode "$mode" --uv "$qualification_base/uv" --uv-sha256 "$uv_sha" --receipt "$qualification_home/product-receipt.json"
qualification_exit=$?
set -e
if sudo /usr/sbin/runuser -u "$qualification_user" -- /usr/bin/test -f "$qualification_home/product-receipt.json"; then
  receipt_size=$(sudo /usr/bin/stat -c %s -- "$qualification_home/product-receipt.json")
  [[ "$receipt_size" -le 4000000 ]]
  sudo /usr/sbin/runuser -u "$qualification_user" -- /usr/bin/head -c 4000000 -- "$qualification_home/product-receipt.json" > "$qualification_receipt"
fi
# Only success permits manager/linger teardown; failures preserve exact evidence
# until this disposable VM ends. No account deletion or process-tree kills.
if [[ "$qualification_exit" -eq 0 && -f "$qualification_receipt" ]]; then
  sudo /usr/bin/systemctl stop "user@$qualification_uid.service"
  sudo /usr/bin/loginctl disable-linger "$qualification_user"
fi
exit "$qualification_exit"
