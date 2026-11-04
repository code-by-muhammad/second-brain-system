# v0.2.1 candidate — protected Linux service lookup aliases

Status: candidate preparation, 4 November 2026. This note is not deployment or native qualification acceptance. Exact successor source, manifest, CI/audit and disposable native-case receipts are pending. The v0.2.0 tag, assets and historical results remain unchanged; its Linux candidate qualification did not complete.

The reproduced Linux refusal occurs while validating an OS service lookup path: `/etc/xdg/systemd/user` is a protected root-owned alias to `../../systemd/user`. Treating that OS-managed alias as a private-runtime link prevents admission before any product unit is installed.

The proposed correction narrowly validates protected root-owned OS lookup aliases and their resolved targets. Private runtime/source paths and unit-definition ownership retain their strict checks. User-controlled, writable, ambiguous or changed paths must still refuse. Do not replace OS links, loosen permissions or bypass the guard to make an installation pass. The exact implementation and its safety tests require source-bound review and receipts.

Qualification remains pending for the exact v0.2.1 source. Do not relabel v0.2.0 Windows/Mac results, old CI counts or an earlier dependency exception as successor passes. Existing-device update qualification, matched Windows preparation helpers and actual receiving-device/client/model/GUI/reboot acceptance are separate. Fresh Mac/Linux onboarding remains unqualified. Follow the current dated owner deployment decision together with the exact release/platform qualification receipts; repository PR merge policy is separate.

Use [device setup](DEVICE-SETUP.md), [verification boundaries](DEVICE-TESTING.md) and the retained [v0.2.0 history](RELEASE-v0.2.0.md). Preserve existing holds, snapshots, original controllers/interpreters, notes, credentials, source exclusions and maintenance ownership. No new schedule or competing Git synchronization is authorized.
