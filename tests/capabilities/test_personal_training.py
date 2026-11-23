from deeptutor.learning.bkt import BKTParameters
from deeptutor.learning.personal_training import fit_rows, score


def test_insufficient_records_do_not_fit():
    assert fit_rows([])["status"] == "insufficient_data"


def test_holdout_uses_only_suffix_for_metrics():
    result = score([[0] * 8 + [1] * 2], BKTParameters())
    assert result["n"] == 2
    assert result["log_loss"] > 0
    assert 0 <= result["brier"] <= 1


def test_fit_is_finite_and_does_not_claim_activation():
    import math

    rows = [
        dict(sequence_id=str(s), timestamp=i, observation_id=f"{s}-{i}", correct=int(i > 8))
        for s in range(3)
        for i in range(25)
    ]
    result = fit_rows(rows)
    assert result["status"] in {"validated_candidate", "rejected"}
    assert result["validation_metrics"]["n"] == 15
    assert math.isfinite(result["validation_metrics"]["log_loss"])
    assert "activated" not in result


def test_unconfigured_personal_model_keeps_default_diagnosis(tmp_path, monkeypatch):
    from deeptutor.learning.personal_training import active_parameters

    monkeypatch.setattr("deeptutor.runtime.home.get_runtime_home", lambda: tmp_path)
    assert active_parameters(object(), "p", "k", "scope") is None
