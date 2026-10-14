import brain_wiki as w

def test_large_note_parts_are_not_marked_complete_after_first_part(brain):
    vault,state=brain
    path=vault/'01-Projects/Large.md'; path.parent.mkdir()
    path.write_text('---\ndate: 2026-10-01\n---\n# Large Source\n'+('The original evidence remains available.\n'*1500),encoding='utf-8')
    packet=w.prepare(4)
    assert len(packet['sources'])==1
    assert packet['sources'][0]['parts']>1
    result={'pages':[{'category':'Concepts','slug':'large-source','title':'Large source','related':[],
                     'claims':[{'text':'The note records available original evidence.','source':'01-Projects/Large.md',
                                'quote':'The original evidence remains available.','status':'historical'}]}]}
    w.apply(packet,result)
    assert w.status()['pending_source_parts']>0
    assert w.prepare()['sources'][0]['part']==2
