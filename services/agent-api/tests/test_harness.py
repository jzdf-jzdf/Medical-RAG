from types import SimpleNamespace

import pytest

import agent_api.services.harness as harness_module
from agent_api.core.config import get_settings


def test_harness_empty_final_response_reports_finish_reason(monkeypatch, tmp_path):
    class FakeHarness:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def run(self, *_args, **_kwargs):
            return SimpleNamespace(final_response=" ", finish_reason="length")

    monkeypatch.setattr(harness_module, "DeepSeekHarness", FakeHarness)
    service = harness_module.HarnessService(get_settings())

    with pytest.raises(RuntimeError, match="finish_reason=length"):
        service._generate_sync("question", tmp_path, "deepseek-chat", "deepseek")
