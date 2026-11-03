#!/usr/bin/env bash
# Explicit manager preparation ONLY on a fresh disposable GitHub Ubuntu VM.
set -euo pipefail
set -o noclobber
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_OS:-}" == Linux ]]
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]]
contract_user="sbci${GITHUB_RUN_ID}${GITHUB_RUN_ATTEMPT}"
if id "$contract_user" >/dev/null 2>&1; then echo 'Fixture user already exists; refusing reuse.' >&2; exit 1; fi
sudo /usr/sbin/useradd --create-home --shell /bin/bash "$contract_user"
contract_uid="$(id -u "$contract_user")"
contract_home="/home/$contract_user"
[[ "$contract_uid" -ge 1000 ]]
[[ "$(getent passwd "$contract_user" | cut -d: -f6)" == "$contract_home" ]]
contract_manager_cleanup() {
  sudo /usr/bin/systemctl stop "user@$contract_uid.service" || true
  sudo /usr/bin/loginctl disable-linger "$contract_user" || true
}
trap contract_manager_cleanup EXIT
sudo /usr/bin/loginctl enable-linger "$contract_user"
sudo /usr/bin/systemctl start "user@$contract_uid.service"
sudo /usr/sbin/runuser -u "$contract_user" -- /usr/bin/env -i HOME="$contract_home" USER="$contract_user" LOGNAME="$contract_user" PATH=/usr/bin:/bin XDG_RUNTIME_DIR="/run/user/$contract_uid" /usr/bin/systemctl --user start dbus.service
git rev-parse HEAD
git diff --quiet HEAD -- app/brain_service.py tests/native_linux_manager_contract.py
sha256sum app/brain_service.py tests/native_linux_manager_contract.py
sudo /usr/bin/install -m 0444 tests/native_linux_manager_contract.py "$contract_home/contract.py"
sudo /usr/bin/install -m 0444 app/brain_service.py "$contract_home/renderer-source.py"
set +e
sudo /usr/bin/timeout --kill-after=10 240 /usr/sbin/runuser -u "$contract_user" -- /usr/bin/env -i HOME="$contract_home" USER="$contract_user" LOGNAME="$contract_user" PATH=/usr/bin:/bin XDG_RUNTIME_DIR="/run/user/$contract_uid" GITHUB_ACTIONS=true /usr/bin/python3 -I -B "$contract_home/contract.py" --expected-user "$contract_user" --renderer-file "$contract_home/renderer-source.py" --receipt "$contract_home/contract-receipt.json" --attestation-only
contract_exit=$?
set -e
mkdir -p reports
if sudo /usr/sbin/runuser -u "$contract_user" -- /usr/bin/test -f "$contract_home/contract-receipt.json"; then
  [[ ! -e reports/linux-manager-contract.json ]]
  sudo /usr/sbin/runuser -u "$contract_user" -- /usr/bin/head -c 4000000 -- "$contract_home/contract-receipt.json" > reports/linux-manager-contract.json
fi
# The EXIT trap touches only the account created exclusively above.
exit "$contract_exit"
