"""One-time cleanup after the 2026-09-14 P5 fact backfill (run by the user).

What it does, in this order, on data/memories.sqlite and data/reflection.sqlite:
1. Backs up every data/*.sqlite and identity.json to data/backups/pre-p5-cleanup-<utc>/.
2. Promotes extracted candidates parked at confidence 0.55 (the old single-unsupported-token
   cap) to active at 0.75, matching the relaxed rule now in fact_extraction.py.
   current_state candidates are handled by step 3 instead.
3. For each current_state topic keeps only the newest fact (by evidence message id) active
   and deprecates the rest, so one topic never has two active states.
4. Deprecates known duplicates/junk from the backfill (older of restated pairs, the
   "'was that'" sentences, a question restated as a fact, a trivial routine line).
5. Rejects the eight pre-P5 reflection candidates (verbatim sentences superseded by the
   extracted fact sentences).
Prints every change and the final active list. Deletes nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil
import sqlite3

DATA = Path("data")
DUPLICATES_AND_JUNK = (
    "천우는 이직 준비 중이다.",
    "천우는 한양이엔지라는 회사에 이직 성공했고, 연봉 협상 중이다.",
    "천우는 과거에 쓴 문장에서 'was that'이라고 표현했다.",
    "천우는 과거 상황에 대한 문장을 'was that'으로 표현한다.",
    "천우는 Perception과 Decision Making을 연결하는 E2E 부분에 대해 설명을 요청하고 있다.",
    "천우는 오늘도 보통처럼 운동을 하고 있다.",
)


def _message_id(evidence_ref: str | None) -> int:
    return int(evidence_ref.split(":")[1]) if evidence_ref and ":" in evidence_ref else -1


def main() -> int:
    now = datetime.now(UTC).isoformat()
    backup = DATA / "backups" / f"pre-p5-cleanup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    backup.mkdir(parents=True)
    for path in [*DATA.glob("*.sqlite"), DATA / "identity.json"]:
        shutil.copy2(path, backup / path.name)
    print(f"backup: {backup}")

    memories = sqlite3.connect(DATA / "memories.sqlite")
    memories.row_factory = sqlite3.Row
    rows = [dict(r) for r in memories.execute(
        "SELECT * FROM memories WHERE source = 'extracted' AND status IN ('active', 'candidate')"
    )]
    for row in rows:
        if (row["status"] == "candidate" and abs(row["confidence"] - 0.55) < 1e-9
                and not row["kind"].startswith("current_state:")):
            print(f"  promote           {row['content'][:70]}")
            memories.execute(
                "UPDATE memories SET status = 'active', confidence = 0.75, updated_at = ? "
                "WHERE id = ?", (now, row["id"]),
            )
    by_topic: dict[str, list[dict]] = {}
    for row in rows:
        if row["kind"].startswith("current_state:"):
            by_topic.setdefault(row["kind"], []).append(row)
    for items in by_topic.values():
        items.sort(key=lambda r: _message_id(r["evidence_ref"]))
        newest = items[-1]
        for row in items[:-1]:
            print(f"  deprecate-state   {row['content'][:70]}")
            memories.execute(
                "UPDATE memories SET status = 'deprecated', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
        if newest["status"] != "active":
            print(f"  activate-state    {newest['content'][:70]}")
            memories.execute(
                "UPDATE memories SET status = 'active', confidence = MAX(confidence, 0.75), "
                "supersedes = ?, updated_at = ? WHERE id = ?",
                (items[-2]["id"] if len(items) > 1 else None, now, newest["id"]),
            )
    for content in DUPLICATES_AND_JUNK:
        for row in memories.execute(
            "SELECT id FROM memories WHERE source = 'extracted' AND content = ? "
            "AND status != 'deprecated'", (content,),
        ).fetchall():
            print(f"  deprecate-junk    {content[:70]}")
            memories.execute(
                "UPDATE memories SET status = 'deprecated', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
    memories.commit()

    reflection = sqlite3.connect(DATA / "reflection.sqlite")
    rejected = reflection.execute(
        "UPDATE reflection_candidates SET status = 'rejected', updated_at = ? "
        "WHERE status = 'candidate'", (now,),
    ).rowcount
    reflection.commit()
    print(f"  reflection candidates rejected: {rejected}")

    print("\nACTIVE memories now:")
    for row in memories.execute(
        "SELECT kind, content, ROUND(confidence, 2) AS c FROM memories "
        "WHERE status = 'active' ORDER BY kind"
    ):
        print(f"  [{row['kind']}] {row['content'][:90]} ({row['c']})")
    count = memories.execute("SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
    print(f"active count: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
