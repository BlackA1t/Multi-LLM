import json

from multi_llm.models import ProviderResult, ResultStatus, RunRecord, RunStatus
from multi_llm.storage import Storage


def test_storage_and_export_do_not_add_secrets(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    run = RunRecord(
        run_id="r1", prompt="hello", participants=["p"], status=RunStatus.PARTIAL, duration_ms=1,
        results=[ProviderResult(
            provider_id="p", requested_model_id="m", actual_model_id=None, model_family_id="f", model_developer="d", auth_method="api_key",
            status=ResultStatus.AUTH_ERROR, error="Authorization: Bearer top-secret-token",
            raw_response={"access_token": "raw-secret-token", "nested": {"authorization": "Bearer nested-secret-token"}},
        )],
    )
    storage.save_run(run)
    output = tmp_path / "run.json"
    storage.export("r1", output)
    text = output.read_text(encoding="utf-8")
    assert "top-secret-token" not in text
    assert "raw-secret-token" not in text
    assert "nested-secret-token" not in text
    assert "[REDACTED]" in text
    assert json.loads(text)["run"]["run_id"] == "r1"
