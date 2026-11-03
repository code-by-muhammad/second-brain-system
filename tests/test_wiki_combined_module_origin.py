"""Prevent an external proposal module or test oracle from satisfying integration checks."""
from pathlib import Path
import brain_wiki as w
import brain_settings as cfg
import brain_store as s

def test_wiki_controls_import_the_same_checkout_modules():
    app=Path(__file__).resolve().parents[1]/'app'
    for module,name in ((w,'brain_wiki.py'),(cfg,'brain_settings.py'),(s,'brain_store.py')):
        assert Path(module.__file__).resolve()==(app/name).resolve()
