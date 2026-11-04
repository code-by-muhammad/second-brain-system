"""Typed, byte-sealed synthetic service snapshot fixtures; no native operations."""
import hashlib,json,uuid
from pathlib import Path


def seed_hold(service,cfg,registration,*,hold='a'*32,stage='prepared',stop_entered=False,forward=False):
    app=cfg.ROOT/'app';app.mkdir(exist_ok=True)
    if not any(app.iterdir()):(app/'fixture_app.py').write_bytes(b'# synthetic original app\n')
    device=cfg.ROOT/'device.json'
    if not device.exists():device.write_text(json.dumps({'python':str(cfg.PYTHON)}))
    target=cfg.ROOT/'backups'/('synthetic-'+uuid.uuid4().hex);(target/'app').mkdir(parents=True)
    raw=device.read_bytes();(target/'0').write_bytes(raw);images={}
    for path in app.rglob('*'):
        if path.is_file() and '__pycache__' not in path.relative_to(app).parts:
            relative=path.relative_to(app);saved=target/'app'/relative;saved.parent.mkdir(parents=True,exist_ok=True)
            payload=path.read_bytes();saved.write_bytes(payload);images[relative.as_posix()]=hashlib.sha256(payload).hexdigest()
    binding=cfg.STATE/'previous-release.json';before=binding.read_bytes() if binding.exists() else None
    if before is not None:(target/'previous-binding.before').write_bytes(before)
    after=b'{"synthetic_controller":true}';(target/'previous-binding.after').write_bytes(after)
    snapshot={'format':3,'root':str(cfg.ROOT),'previous':{'python':registration['interpreter']},
        'registration':dict(registration),'files':[{'path':str(device),'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':None}],
        'app_sha256':images,'previous_binding':{'before_sha256':hashlib.sha256(before).hexdigest() if before is not None else None,
        'after_sha256':hashlib.sha256(after).hexdigest()}}
    if forward:
        checkout=cfg.ROOT/'releases/v9.9.9';checkout.mkdir(parents=True,exist_ok=True)
        payload=b'# synthetic verified forward app\n';expected={'fixture_app.py':hashlib.sha256(payload).hexdigest()}
        manifest={'version':'9.9.9','repository':'code-by-muhammad/second-brain-system','sha256':{'app/'+name:digest for name,digest in expected.items()}}
        rendered=json.dumps(manifest).encode();(checkout/'release-manifest.json').write_bytes(rendered)
        snapshot.update(controller_checkout=str(checkout),controller_identity={'repository':manifest['repository'],'tag':'v9.9.9','manifest_sha256':hashlib.sha256(rendered).hexdigest()})
        raw=json.dumps({'python':str(cfg.PYTHON),'release_root':str(checkout),'version':'9.9.9'}).encode()
        snapshot['files'][0]['after_sha256']=hashlib.sha256(raw).hexdigest();device.write_bytes(raw)
        for path in app.rglob('*'):
            if path.is_file():path.unlink()
        (app/'fixture_app.py').write_bytes(payload)
    raw=json.dumps(snapshot).encode();(target/'snapshot.json').write_bytes(raw)
    value={'hold_id':hold,'root':str(cfg.ROOT),'snapshot':str(target),'snapshot_sha256':hashlib.sha256(raw).hexdigest(),
        'stage':stage,'stop_entered':stop_entered}
    if stage=='stopped':value['empty_at_filetime']=123
    (cfg.STATE/'service-transaction.json').write_text(json.dumps(value));return value
