"""Gate internal memory maintenance without adding user-facing chores."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaintenanceMode(StrEnum):
    SHADOW = "shadow"
    CANDIDATE = "candidate"
    OPERATIVE = "operative"


@dataclass(frozen=True)
class MaintenanceGates:
    analysis_runtime_ready: bool = False
    held_out_gate_passed: bool = False
    real_audit_passed: bool = False
    automatic_update_policy_approved: bool = False


@dataclass(frozen=True)
class MaintenanceRoute:
    mode: MaintenanceMode
    retrieve_active_memory: bool
    enqueue_turn_analysis: bool
    run_background_analysis: bool
    create_internal_candidates: bool
    confirm_uncertainty_in_conversation: bool
    auto_apply_explicit_updates: bool
    blocks_response: bool = False
    requires_user_command: bool = False


class ConversationMaintenancePolicy:
    """Turn quality gates into one interface-independent maintenance route."""

    def __init__(self, gates: MaintenanceGates | None = None) -> None:
        self._gates = gates or MaintenanceGates()

    def route(self) -> MaintenanceRoute:
        gates = self._gates
        candidate_ready = (
            gates.analysis_runtime_ready and gates.held_out_gate_passed
        )
        operative_ready = (
            candidate_ready
            and gates.real_audit_passed
            and gates.automatic_update_policy_approved
        )
        if operative_ready:
            mode = MaintenanceMode.OPERATIVE
        elif candidate_ready:
            mode = MaintenanceMode.CANDIDATE
        else:
            mode = MaintenanceMode.SHADOW
        return MaintenanceRoute(
            mode=mode,
            retrieve_active_memory=True,
            enqueue_turn_analysis=True,
            run_background_analysis=gates.analysis_runtime_ready,
            create_internal_candidates=candidate_ready,
            confirm_uncertainty_in_conversation=candidate_ready,
            auto_apply_explicit_updates=operative_ready,
        )
