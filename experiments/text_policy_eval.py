"""Isolated T2 comparison: synthetic data only, real loopback model, no workers.

The old companion_dialogue_eval remains a separate historical multi-session
probe. This runner compares frozen policies and never uses data/identity.json.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
from time import perf_counter
from typing import Any, TextIO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from configs.dialogue.evaluation import SCENES, VERSION
from configs.dialogue.postfix_evaluation import SCENES as POSTFIX_SCENES
from configs.dialogue.postfix_evaluation import VERSION as POSTFIX_VERSION

from companion.adapters.llama_cpp import SAMPLING, LlamaCppHttpChatModel, _extract_text
from companion.adapters.sqlite_repository import SqliteConversationRepository
from companion.context import ConversationContextBuilder
from companion.contracts import ChatRequest, ChatResult, ConversationMessage
from companion.core import CompanionCore
from companion.dialogue.policy import POLICY_NAMES, build_dialogue_policy
from companion.grounding import ClaimEvidence, ClaimViolation, ResponseClaimGate
from companion.identity import CompanionIdentity
from companion.memory import ActiveMemoryRetriever, SqliteMemoryRepository
from companion.open_loops import ActiveOpenLoopRetriever, SqliteOpenLoopRepository
from companion.ports import ChatModel
from companion.verbal_style import VerbalStylePlanner, load_verbal_style

IDENTITY = CompanionIdentity(
    "겨울이",
    "천우와 대화를 이어가는 로컬 동반자야.",
    ("솔직함", "차분함"),
    ("사용자의 선택 존중", "근거 있는 판단"),
    ("친밀함이나 관계를 임의로 확정하지 않아.",),
    ("실제 대화 밖의 경험과 실행하지 않은 능력을 꾸미지 않아.",),
    "synthetic-t2-v1",
)
PROFILE = "reference_conversation"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("evaluation does not follow redirects")


class ObservedLocalModel:
    """Use the production payload/decoder while retaining otherwise-lost telemetry."""

    def __init__(self, url: str, seed: int) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("evaluation requires an explicit credential-free HTTP loopback URL")
        self.adapter = LlamaCppHttpChatModel(url.rstrip("/"), timeout_seconds=45)
        self.seed = seed
        self.calls: list[dict[str, Any]] = []
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def metadata(self) -> dict[str, Any]:
        def get(path: str) -> dict[str, Any]:
            with self.opener.open(self.adapter.base_url + path, timeout=5) as response:
                value: dict[str, Any] = json.load(response)
                return value

        get("/health")
        models = get("/v1/models")
        props = get("/props")
        return dict(
            model=Path(props["model_path"]).name,
            build=props["build_info"],
            slots=props["total_slots"],
            meta=models["data"][0].get("meta", {}),
            server_context=props.get("default_generation_settings", {}).get("n_ctx"),
            weight_digest="not supplied by server; filename/size/architecture only",
        )

    def generate(self, request: ChatRequest) -> ChatResult:
        sent = replace(request, seed=self.seed)
        record: dict[str, Any] = dict(request=asdict(sent), seconds=None, text=None)
        self.calls.append(record)
        started = perf_counter()
        try:
            endpoint = self.adapter._endpoint()
            payload = self.adapter._payload(sent, stream=False)
            http_request = Request(
                endpoint, data=payload, method="POST", headers={"Content-Type": "application/json"}
            )
            with self.opener.open(http_request, timeout=self.adapter.timeout_seconds) as response:
                raw = response.read()
            body = json.loads(raw)
            text = _extract_text(raw, endpoint)
            record.update(
                text=text,
                finish_reason=body["choices"][0].get("finish_reason"),
                usage=body.get("usage"),
                timings=body.get("timings"),
            )
            return ChatResult(text)
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["seconds"] = perf_counter() - started


class AuditClaimGate(ResponseClaimGate):
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def violations(self, text: str, evidence: ClaimEvidence) -> tuple[ClaimViolation, ...]:
        found = super().violations(text, evidence)
        self.checks.append(dict(text=text, violations=[asdict(item) for item in found]))
        return found


def make_core(
    root: Path,
    model: ChatModel,
    policy: str,
    seed: int,
    prefix: tuple[ConversationMessage, ...] = (),
) -> tuple[CompanionCore, AuditClaimGate]:
    conversations = SqliteConversationRepository(root / "conversations.sqlite")
    for message in prefix:
        conversations.append(message)
    memory = SqliteMemoryRepository(root / "memories.sqlite")
    loops = SqliteOpenLoopRepository(root / "loops.sqlite")
    gate = AuditClaimGate()
    selected = build_dialogue_policy(
        policy,
        style_planner=VerbalStylePlanner(
            load_verbal_style(PROFILE),
            rng=random.Random(seed),
        ),
    )
    core = CompanionCore(
        model,
        conversations,
        ConversationContextBuilder(max_messages=12, max_characters=4000),
        identity=IDENTITY,
        memory_retriever=ActiveMemoryRetriever(memory),
        memory_repository=memory,
        open_loop_repository=loops,
        open_loop_retriever=ActiveOpenLoopRetriever(loops),
        dialogue_policy=selected,
        response_claim_gate=gate,
    )
    return core, gate


def fixed_prefix(turns: tuple[str, ...]) -> tuple[ConversationMessage, ...]:
    return tuple(
        message
        for text in turns[:-1]
        for message in (
            ConversationMessage("user", text),
            ConversationMessage("assistant", "응, 듣고 있어."),
        )
    )


def emit(output: TextIO, record: dict[str, Any]) -> None:
    output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    output.flush()
    os.fsync(output.fileno())


def run(
    output: TextIO,
    model: ObservedLocalModel,
    *,
    split: str,
    mode: str,
    policies: tuple[str, ...],
    seed: int,
    scenes: tuple[dict[str, Any], ...] | None = None,
) -> None:
    selected_scenes = SCENES if scenes is None else scenes
    for scene_index, scene in enumerate(s for s in selected_scenes if s["split"] == split):
        # Rotate order across scenes to reduce systematic position/warm-up bias.
        offset = scene_index % len(policies)
        order = policies[offset:] + policies[:offset]
        turns = tuple(scene["turns"])
        for selected in order:
            with tempfile.TemporaryDirectory(prefix="winter-policy-eval-") as directory:
                prefix = fixed_prefix(turns) if mode == "fixed" else ()
                core, gate = make_core(Path(directory), model, selected, seed, prefix)
                selected_turns = (turns[-1],) if mode == "fixed" else turns
                for turn_index, text in enumerate(selected_turns):
                    model.calls.clear()
                    gate.checks.clear()
                    started = perf_counter()
                    record: dict[str, Any] = dict(
                        type="turn",
                        scene=scene["id"],
                        split=split,
                        mode=mode,
                        policy=selected,
                        turn_index=turn_index,
                        user=text,
                        seed=seed,
                        first_token_seconds=None,
                    )
                    try:
                        response = core.respond_to_text(text)
                        record.update(response=asdict(response), status="ok")
                    except Exception as error:
                        record.update(status="failed", error=f"{type(error).__name__}: {error}")
                        raise
                    finally:
                        record.update(
                            seconds=perf_counter() - started,
                            calls=model.calls,
                            claim_checks=gate.checks,
                            human_review="pending",
                        )
                        emit(output, record)
                        print(
                            f"{scene['id']} {mode} {selected} turn={turn_index} "
                            f"{record['status']} {record['seconds']:.2f}s",
                            flush=True,
                        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=("development", "reserved"), required=True)
    parser.add_argument("--mode", choices=("fixed", "rollout"), required=True)
    parser.add_argument("--policy", choices=POLICY_NAMES, action="append")
    parser.add_argument("--seed", type=int, default=13201)
    parser.add_argument("--suite", choices=("original", "postfix"), default="original")
    args = parser.parse_args()
    if args.suite == "postfix" and args.split != "development":
        parser.error("postfix scenes are development regressions, not reserved/held-out")
    scenes = POSTFIX_SCENES if args.suite == "postfix" else SCENES
    model = ObservedLocalModel(args.url, args.seed)
    meta = model.metadata()
    # Exclusive creation prevents silent overwrite of an earlier failure/result.
    with args.output.open("x", encoding="utf-8") as output:
        source_root = Path(__file__).resolve().parents[1]
        hashes = {
            name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
            for name in (
                "experiments/text_policy_eval.py",
                "src/companion/dialogue/policy.py",
                "configs/dialogue/evaluation.py",
                "configs/verbal_style/reference_conversation.py",
                "configs/dialogue/postfix_evaluation.py",
                "src/companion/core.py",
                "src/companion/grounding.py",
                "src/companion/dialogue/claim_language.py",
                "src/companion/dialogue/context_assembler.py",
            )
        }
        emit(
            output,
            dict(
                type="manifest",
                at=datetime.now(UTC).isoformat(),
                model=meta,
                identity=asdict(IDENTITY),
                sampling=SAMPLING,
                seed=args.seed,
                scene_version=POSTFIX_VERSION if args.suite == "postfix" else VERSION,
                suite=args.suite,
                hashes=hashes,
                fixture_only=True,
                policies=args.policy or POLICY_NAMES,
                split=args.split,
                mode=args.mode,
                first_token_measurement="not measured: non-streaming HTTP",
                seed_support="requested; repeatability observed in warmup, not guaranteed",
            ),
        )
        warm_request = ChatRequest(
            "안녕", messages=(ConversationMessage("user", "안녕"),), max_tokens=16
        )
        first = model.generate(warm_request).text
        second = model.generate(warm_request).text
        emit(output, dict(type="warmup", identical=first == second, calls=model.calls))
        run(
            output,
            model,
            split=args.split,
            mode=args.mode,
            policies=tuple(args.policy or POLICY_NAMES),
            seed=args.seed,
            scenes=scenes,
        )
        final_meta = model.metadata()
        if final_meta != meta:
            emit(output, dict(type="model_changed", before=meta, after=final_meta))
            raise RuntimeError("model metadata changed during evaluation")
        emit(output, dict(type="complete", at=datetime.now(UTC).isoformat(), model=final_meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
