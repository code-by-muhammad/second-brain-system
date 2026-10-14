"""Claude Code hooks. Inject policy and request one end-of-work capture without looping."""
import sys,json
from pathlib import Path
POLICY='Second brain is configured as MCP server second-brain. Call brain_context at session start; search then open source notes before recalling prior work. For Roman Urdu include an English search paraphrase. Save meaningful outcomes and decisions through brain_capture with stable event_id, accurate source_reference and evidence_paths. Never capture credentials or assume profile facts. Update only derived memory pages with sources and current SHA256. The local service links captures to daily notes and syncs Git; do not duplicate this log or run a competing sync.'
def respond(data):
    event=data.get('hook_event_name')
    if event=='SessionStart':
        return {'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':POLICY}}
    if event=='Stop' and not data.get('stop_hook_active'):
        # Avoid an extra model turn for greetings and already-saved work.
        try:
            rows=Path(data['transcript_path']).read_text(encoding='utf-8').splitlines()
            used=[]
            for line in reversed(rows):
                row=json.loads(line); msg=row.get('message',{}); content=msg.get('content',[])
                if row.get('type')=='user' and (isinstance(content,str) or any(x.get('type')=='text' for x in content if isinstance(x,dict))): break
                if isinstance(content,list): used.extend(x.get('name','') for x in content if isinstance(x,dict) and x.get('type')=='tool_use')
            if any(n.endswith('brain_capture') for n in used): return {}
            meaningful=any(any(word in n.lower() for word in ('edit','write','bash','powershell','websearch','webfetch','update_current')) for n in used)
            if not meaningful: return {}
        except (OSError,KeyError,ValueError,TypeError):
            return {} # Never block an unrelated session when its transcript cannot be read.
        return {'decision':'block','reason':'Before finishing, if this turn performed meaningful work, save one concise milestone using brain_capture and report its receipt. Skip trivial conversation and avoid repeating an already saved milestone. Use session '+str(data.get('session_id','unknown'))+' plus a milestone name as stable event_id. If already saved, or the service is unavailable, finish without looping; report save failure honestly.'}
    return {}
if __name__=='__main__':
    try: print(json.dumps(respond(json.load(sys.stdin))))
    except Exception: print('{}')
