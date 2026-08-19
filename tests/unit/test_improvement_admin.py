from pathlib import Path

from companion.improvement_admin import main
from companion.improvements import SqliteImprovementLedger


def test_admin_prints_only_aggregate_state(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "improvements.sqlite"
    ledger = SqliteImprovementLedger(path)
    ledger.observe(
        title="private title must not be printed",
        problem="private problem must not be printed",
        evidence=("conversation:private",),
    )

    assert main(["--ledger", str(path)]) == 0
    output = capsys.readouterr().out

    assert "improvements: total=1" in output
    assert "observed: 1" in output
    assert "implementation_approvals: 0" in output
    assert "adoption_approvals: 0" in output
    assert "cycle_records: 0" in output
    assert "private" not in output
