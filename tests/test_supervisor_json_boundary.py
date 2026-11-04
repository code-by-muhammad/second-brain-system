"""Portable decoder-fault boundary and a known fitting nested-data control."""
import json
from test_supervisor_fault_recovery import test_actual_supervisor_recovery_keeps_owned_children as _observe


def test_injected_progress_decoder_RecursionError_uses_existing_fallback(brain, monkeypatch):
    raw = '{"synthetic_decoder_fault":true}'
    original = json.loads

    def loads(value, *args, **kwargs):
        if value == raw:
            raise RecursionError('synthetic decoder boundary')
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, 'loads', loads)
    _observe(brain, monkeypatch, 'injected-decoder-error', raw, None)


def test_known_fitting1100_progress_details_keep_owned_children(brain, monkeypatch):
    raw = '{"status":"pending","details":' + '[' * 1100 + '0' + ']' * 1100 + '}'
    assert len(raw) < 2400
    _observe(brain, monkeypatch, 'fitting-details-control', raw, None)
