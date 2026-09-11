"""#137 diagnostic only: identical evidence/history, minimal vs legacy raw output.

No production routing, repair, Worker, private identity or persistent user DB.
Private Core preparation is deliberately confined to this experiment.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import tempfile
from typing import Any, TextIO

from configs.dialogue.postfix_evaluation import SCENES
from experiments.text_policy_eval import IDENTITY, ObservedLocalModel, emit, make_core

from companion.adapters.llama_cpp import SAMPLING
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.dialogue.context_assembler import ContextAssembler
from companion.grounding import ClaimEvidence, ResponseClaimGate
from companion.response_review import ResponseReviewer

VERSION = "context-ablation-v1-2026-09-11"
SEEDS = (13701, 13702, 13703)
MINIMAL_INSTRUCTION = (
    "지금 사용자 말의 대상과 요청을 유지하며 자연스러운 한국어로 답해. "
    "제공된 기억과 대화에 없는 사실은 모른다고 말하고 추측해 기억으로 만들지 마. "
    "실제 수행하지 않은 일이나 신체 활동, 제공되지 않은 능력을 주장하지 마."
)
# Fixed synthetic prior replies; never generate different histories for the arms.
PRIOR_REPLIES = {
    "dev_mixed": "설정을 바꾼 뒤부터 실행이 안 되는 거구나.",
    "dev_correction": "오늘 뭔가 잘 풀렸나 보네ㅋㅋ",
    "dev_view": "넣고 싶은 기능이 많구나.",
    "dev_memory": "응, 아직 네 음식 취향은 들은 적 없어.",
    "dev_end": "응, 오늘은 여기까지 하자.",
    "physical_boundary": "나는 차를 직접 마실 수는 없어. 여기서 대화할 수 있어.",
}


class NoGeneration:
    def generate(self, request: ChatRequest) -> ChatResult:
        raise RuntimeError("preparation must not invoke a model")


class PairedAssembler(ContextAssembler):
    minimal: ChatRequest | None = None

    def assemble(self, **kwargs: Any) -> ChatRequest:
        full = super().assemble(**kwargs)
        minimal_args = dict(kwargs)
        minimal_args.update(
            style_instruction=None,
            grounding_instruction=MINIMAL_INSTRUCTION,
            dialogue_instruction="",
        )
        # Preserve exact identity, selected memory IDs/content, recalled facts,
        # history and generation budget. Only the instruction blocks differ.
        self.minimal = super().assemble(**minimal_args)
        return full


@dataclass(frozen=True)
class PreparedPair:
    minimal: ChatRequest
    full: ChatRequest
    evidence: ClaimEvidence
    behavior: str
    focus: str | None
    memory_focus: str | None


def prepare_pair(root: Path, scene: dict[str, Any]) -> PreparedPair:
    turns = scene["turns"]
    is_memory = scene["id"] == "known_memory"
    prefix = () if is_memory else (
        ConversationMessage("user", turns[0]),
        ConversationMessage("assistant", PRIOR_REPLIES[scene["id"]]),
    )
    core, _ = make_core(root, NoGeneration(), "legacy", SEEDS[0], prefix)
    if is_memory:
        core.respond_to_text(turns[0])  # Explicit synthetic save, no LLM call.
    assembler = PairedAssembler()
    core._context_assembler = assembler
    turn = core._prepare(turns[-1])
    if assembler.minimal is None:
        raise RuntimeError("no request captured")
    if turn.worker_request or turn.owned_work_request:
        raise RuntimeError("Worker routing is forbidden in this experiment")
    return PreparedPair(
        minimal=assembler.minimal,
        full=turn.request,
        evidence=core._claim_evidence(turn),
        behavior=turn.dialogue_plan.behavior,
        focus=turn.dialogue_plan.focus,
        memory_focus=turn.memory_focus,
    )


def run(
    output: TextIO,
    model: ObservedLocalModel,
    *,
    scenes: tuple[dict[str, Any], ...] = SCENES,
    seeds: tuple[int, ...] = SEEDS,
) -> None:
    gate = ResponseClaimGate()
    reviewer = ResponseReviewer()
    for scene_index, scene in enumerate(scenes):
        with tempfile.TemporaryDirectory(prefix="winter-ablation-") as directory:
            pair = prepare_pair(Path(directory), scene)
            for repeat, seed in enumerate(seeds):
                order = ("minimal", "full") if (scene_index + repeat) % 2 == 0 else (
                    "full", "minimal"
                )
                for arm in order:
                    request = replace(getattr(pair, arm), seed=seed)
                    model.seed = seed
                    model.calls.clear()
                    record: dict[str, Any] = dict(
                        type="sample", scene=scene["id"], arm=arm, seed=seed,
                        request=asdict(request), evidence=asdict(pair.evidence),
                        behavior=pair.behavior, human_review="pending",
                        audit_only=True, repair_executed=False,
                    )
                    try:
                        draft = model.generate(request).text
                        violations = gate.violations(draft, pair.evidence)
                        style_instruction = reviewer.repair_instruction(
                            pair.behavior, draft, focus=pair.focus,
                            memory_focus=pair.memory_focus,
                        )
                        record.update(
                            status="ok", raw=draft,
                            claim_flags=[asdict(v) for v in violations],
                            legacy_style_would_repair=style_instruction is not None,
                        )
                    except Exception as error:
                        record.update(status="failed", error=f"{type(error).__name__}: {error}")
                        raise
                    finally:
                        record["calls"] = model.calls
                        emit(output, record)
                        print(
                            f"{scene['id']} {arm} seed={seed} {record['status']}", flush=True
                        )


def source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), root / "experiments/text_policy_eval.py"]
    for directory in ("src/companion", "configs"):
        paths.extend(sorted((root / directory).rglob("*.py")))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    model = ObservedLocalModel(args.url, SEEDS[0])
    before = model.metadata()
    hashes = source_hashes()
    with args.output.open("x", encoding="utf-8") as output:
        emit(output, dict(
            type="manifest", at=datetime.now(UTC).isoformat(), version=VERSION,
            model=before, identity=asdict(IDENTITY), sampling=SAMPLING, seeds=SEEDS,
            hashes=hashes, fixture_only=True, scenes=SCENES,
            prior_replies=PRIOR_REPLIES, expected_samples=len(SCENES) * len(SEEDS) * 2,
            scope="fixed-history synthetic development probe; not production/held-out",
            timing="non-streaming full generation, not TTFT; no repair calls",
        ))
        warm = ChatRequest("안녕", (ConversationMessage("user", "안녕"),), max_tokens=16)
        for _ in range(2):
            model.generate(warm)
        emit(output, dict(type="warmup", calls=model.calls))
        run(output, model)
        after = model.metadata()
        if after != before or source_hashes() != hashes:
            raise RuntimeError("model metadata or source changed during evaluation")
        emit(output, dict(type="complete", at=datetime.now(UTC).isoformat(), model=after))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
