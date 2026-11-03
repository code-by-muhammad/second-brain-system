"""Synthetic existing-daily BOM, untouched owner regions and CAS controls."""
import codecs,json
from datetime import datetime
import pytest
import brain_maintenance as m
import brain_store as s
import brain_wiki as w


def fixture_daily(brain,bom):
    vault,state=brain
    receipt=s.capture('daily-bom-case','Synthetic BOM milestone','Synthetic verified result.','synthetic-test','fixture only')
    capture=s.safe_path(receipt['path']);raw=capture.read_text(encoding='utf-8');meta=w.meta(raw)
    when=datetime.fromisoformat(meta['captured_at'])
    daily=vault/'05-Daily'/(when.strftime('%Y-%m-%d-%A')+'.md');daily.parent.mkdir()
    prefix='# Owner daily\n\nOwner prefix Î±.\n\n'.encode()
    suffix='\n## Owner next section\nOwner suffix Î².\n'.encode()
    original=(codecs.BOM_UTF8 if bom else b'')+prefix+b'## Work Log\nExisting owner log line.\n'+suffix
    daily.write_bytes(original)
    return daily,capture,raw,meta,when,original,prefix,suffix,state


@pytest.mark.parametrize('bom',[False,True])
def test_existing_daily_bom_and_owner_regions_are_preserved(brain,bom):
    daily,capture,raw,meta,when,original,prefix,suffix,state=fixture_daily(brain,bom)
    source_before=capture.read_bytes()
    assert m._daily_link(capture,raw,meta,when) is True
    after=daily.read_bytes()
    assert after.startswith(codecs.BOM_UTF8)==bom
    body=after.removeprefix(codecs.BOM_UTF8)
    assert body.startswith(prefix+b'## Work Log\nExisting owner log line.\n') and body.endswith(suffix)
    assert body.count(b'<!-- brain-capture:daily-bom-case -->')==1
    assert capture.read_bytes()==source_before
    assert (state/'write-backups'/s.digest(original)).read_bytes()==original
    audit=json.loads((state/'audit.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert audit['before']==s.digest(original) and audit['after']==s.digest(after)
    assert m._daily_link(capture,raw,meta,when) is False and daily.read_bytes()==after


@pytest.mark.parametrize('bom',[False,True])
@pytest.mark.parametrize('window',['after-read','before-writer-cas'])
def test_owner_cas_changes_preserve_exact_current_bom_and_bytes(brain,monkeypatch,bom,window):
    daily,capture,raw,meta,when,original,prefix,suffix,state=fixture_daily(brain,bom)
    owner=codecs.BOM_UTF8+b'# Concurrent owner bytes; preserve exactly.\r\n'
    source_before=capture.read_bytes();events=[]
    if window=='after-read':
        read=m._read_derived
        def edit(path):
            result=read(path)
            if path==daily:daily.write_bytes(owner);events.append('owner edit')
            return result
        monkeypatch.setattr(m,'_read_derived',edit)
    else:
        write=s._write
        def edit(path,text,kind,*args,**kwargs):
            if path==daily:
                assert kwargs['expected_sha256']==s.digest(original)
                daily.write_bytes(owner);events.append('owner edit')
            return write(path,text,kind,*args,**kwargs)
        monkeypatch.setattr(s,'_write',edit)
    with pytest.raises(ValueError,match='[Cc]hange'):m._daily_link(capture,raw,meta,when)
    assert events==['owner edit'] and daily.read_bytes()==owner and capture.read_bytes()==source_before
