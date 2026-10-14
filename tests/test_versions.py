import tomllib
from pathlib import Path
import brain_settings
from test_portability import installer

def test_installer_runtime_and_project_version_match():
    data=tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())
    assert data['project']['version']==brain_settings.VERSION==installer.VERSION
