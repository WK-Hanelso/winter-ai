"""Privacy-minimal monitoring for Winter's days and autonomous beliefs."""

from __future__ import annotations

from dataclasses import dataclass

from companion.belief_reflection import SqliteBeliefProposalRepository
from companion.beliefs import SqliteBeliefRepository
from companion.winter_events import SqliteWinterEventRepository, WinterDay


@dataclass(frozen=True)
class LivedContinuitySummary:
    day: WinterDay
    active_beliefs: int
    candidate_beliefs: int
    recent_activations: int
    recent_blocks: int
    recent_block_reasons: tuple[str, ...]
    safe_shadow_proposals: int = 0
    blocked_shadow_proposals: int = 0
    shadow_block_reasons: tuple[str, ...] = ()


def summarize_lived_continuity(
    events: SqliteWinterEventRepository,
    beliefs: SqliteBeliefRepository,
    local_date: str,
    proposals: SqliteBeliefProposalRepository | None = None,
) -> LivedContinuitySummary:
    belief_rows = beliefs.list()
    decisions = beliefs.list_activation_decisions(limit=50)
    reasons = {
        reason
        for decision in decisions
        if not decision.activated
        for reason in decision.reasons
    }
    proposal_rows = proposals.list() if proposals is not None else ()
    proposal_reasons = {
        reason
        for observation in proposal_rows
        if not observation.safe
        for reason in observation.reason_codes
    }
    return LivedContinuitySummary(
        day=events.summarize_day(local_date),
        active_beliefs=sum(belief.status == "active" for belief in belief_rows),
        candidate_beliefs=sum(belief.status == "candidate" for belief in belief_rows),
        recent_activations=sum(decision.activated for decision in decisions),
        recent_blocks=sum(not decision.activated for decision in decisions),
        recent_block_reasons=tuple(sorted(reasons)),
        safe_shadow_proposals=sum(observation.safe for observation in proposal_rows),
        blocked_shadow_proposals=sum(
            not observation.safe for observation in proposal_rows
        ),
        shadow_block_reasons=tuple(sorted(proposal_reasons)),
    )


def render_lived_continuity(summary: LivedContinuitySummary) -> str:
    day = summary.day
    kinds = ", ".join(f"{name}={count}" for name, count in day.kind_counts) or "없음"
    sources = ", ".join(f"{name}={count}" for name, count in day.source_counts) or "없음"
    reasons = ", ".join(summary.recent_block_reasons) or "없음"
    shadow_reasons = ", ".join(summary.shadow_block_reasons) or "없음"
    return "\n".join(
        (
            f"겨울이의 {day.local_date}",
            f"- 실제 사건: {day.event_count} ({kinds})",
            f"- 인터페이스: {sources}",
            f"- 활성 관점: {summary.active_beliefs}",
            f"- 검토 중 관점: {summary.candidate_beliefs}",
            f"- 최근 자율 활성화/차단: {summary.recent_activations}/"
            f"{summary.recent_blocks}",
            f"- 최근 차단 이유: {reasons}",
            f"- Shadow 관점 제안 통과/차단: {summary.safe_shadow_proposals}/"
            f"{summary.blocked_shadow_proposals}",
            f"- Shadow 제안 차단 이유: {shadow_reasons}",
        )
    )
