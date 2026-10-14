"""Import native retrieval/runtime libraries on every CI OS, without private notes/models."""
import importlib, tempfile, os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
with tempfile.TemporaryDirectory() as folder:
    os.environ['BRAIN_SYSTEM_ROOT']=folder
    for name in ('onnxruntime','fastembed','basic_memory','fastmcp','brain_server'):
        if name=='brain_server':
            import json, hashlib
            state=Path(folder)/'state'; state.mkdir(exist_ok=True)
            (state/'access.json').write_text(json.dumps({'token_sha256':hashlib.sha256(b'synthetic-ci-token').hexdigest()}))
        importlib.import_module(name)
    print('Native library and MCP imports passed on',sys.platform)
    import logging
    logging.shutdown()
    from loguru import logger
    logger.remove()
