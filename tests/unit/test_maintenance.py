from companion.adapters.fake import FakeChatModel, InMemoryConversationRepository
from companion.core import CompanionCore
from companion.maintenance import (
    ConversationMaintenancePolicy,
    MaintenanceGates,
    MaintenanceMode,
)
from companion.turn_understanding import SqliteTurnUnderstandingRepository


def test_default_route_is_zero_chore_shadow_mode() -> None:
    route = ConversationMaintenancePolicy().route()

    assert route.mode == MaintenanceMode.SHADOW
    assert route.retrieve_active_memory
    assert route.enqueue_turn_analysis
    assert not route.run_background_analysis
    assert not route.create_internal_candidates
    assert not route.auto_apply_explicit_updates
    assert not route.blocks_response
    assert not route.requires_user_command


def test_candidate_route_still_confirms_inside_conversation() -> None:
    route = ConversationMaintenancePolicy(
        MaintenanceGates(
            analysis_runtime_ready=True,
            held_out_gate_passed=True,
        )
    ).route()

    assert route.mode == MaintenanceMode.CANDIDATE
    assert route.run_background_analysis
    assert route.create_internal_candidates
    assert route.confirm_uncertainty_in_conversation
    assert not route.auto_apply_explicit_updates
    assert not route.requires_user_command


def test_automatic_update_requires_every_activation_gate() -> None:
    approved = MaintenanceGates(True, True, True, True)

    route = ConversationMaintenancePolicy(approved).route()

    assert route.mode == MaintenanceMode.OPERATIVE
    assert route.auto_apply_explicit_updates
    for gates in (
        MaintenanceGates(False, True, True, True),
        MaintenanceGates(True, False, True, True),
        MaintenanceGates(True, True, False, True),
        MaintenanceGates(True, True, True, False),
    ):
        assert not ConversationMaintenancePolicy(
            gates
        ).route().auto_apply_explicit_updates


def test_core_enqueues_internal_analysis_without_user_command(tmp_path) -> None:
    turns = SqliteTurnUnderstandingRepository(tmp_path / "turns.sqlite")
    core = CompanionCore(
        FakeChatModel(),
        InMemoryConversationRepository(),
        turn_understanding_repository=turns,
        turn_source="voice",
    )

    response = core.respond_to_text("오늘은 마음이 조금 편해졌어")

    events = turns.list(status="pending")
    assert len(events) == 1
    assert events[0].request.user_text == "오늘은 마음이 조금 편해졌어"
    assert events[0].source == "voice"
    assert response.shadow_turn_id == events[0].id
    assert response.maintenance_mode == MaintenanceMode.SHADOW
