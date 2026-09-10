# ADR 0034 — 기존 대화 정책 보존과 문맥 기반 후보

- 상태: Accepted for explicit experiment; 기본값 승격 아님
- 날짜: 2026-09-10
- 범위: Epic #127 / T2.1 #131
- 선행: [ADR 0033](0033-character-snapshot-context-assembler.md)

## 결정

`DialoguePolicyInput → DialoguePolicyTurn` 경계에 기존 Director/VerbalStyle을 감싼
`LegacyDialoguePolicy`와 `ContextualDialoguePolicy` 후보를 둔다. 입력은 현재 문장/기존 act,
선택된 최근 대화·기억·현재 상태 이력·미완성 이야기다. 출력은 `DialoguePlan`과 한 번 결정한
`TurnStyle`이다. 초기화는 Python `TextRuntimeConfig.dialogue_policy`가 선택한다.
Core 생성자의 기존 옵션은 유지하고 끝에 선택적 policy 주입점만 추가한다.

| 선택값 | 대화 행동 지침 | 말높임 선택 |
| --- | --- | --- |
| `legacy` (기본) | 기존 Director | 기존 분포 |
| `contextual-moves-only` | 문맥 해석 지침 | 기존 분포 |
| `stable-register-only` | 기존 Director | 명시적 요청/합의 우선, 무작위 추첨 제거 |
| `contextual` | 문맥 해석 지침 | 명시적 요청/합의 우선, 무작위 추첨 제거 |

이는 두 변경의 효과를 각각 측정하기 위한 비교 축이다. 후보는 감정을 판정하는 새 모델이나
학습된 관계 엔진이 아니다. 동일한 Core LLM이 기존 대화 입력과 짧은 지침으로 의도/분위기를
해석하게 하는 prompt 기반 가설이다. 예를 들어 감정 단어가 있더라도 실용적 요청을 함께
충족하라는 지침을 주며, Python에서 그 감정을 확정하지 않는다.

후보의 지침은 문맥/기억/직접 합의 선호/미완성 이야기의 가용성에 따라 달라진다. 선호는
기존에 검색된 active preference 중 `user_explicit`, `user_direct`, `user_edit`만 직접 합의로
취급한다. 검색 변경이나 새로운 선호 추출은 없다. 선택되지 않은 장기 선호를 후보가 항상
안다고 가정하면 안 된다. 원문은 기존 assembler가 전달하며 평가 정답/루브릭은 넣지 않는다.

stable-register 경로는 기존 프로필의 길이·단어 수·어휘 지침을 보존하고 확률적 말높임
추첨만 제거한다. 명시적 요청/합의를 모델이 우선하며, 없으면 기존 프로필의 고정/다수
말높임이 fallback이다. `VerbalStylePlan.register="contextual"`은 모델이 문맥으로 결정해야
한다는 계획 metadata이지 실제 생성 문장의 말높임을 측정했다는 뜻이 아니다. Voice 출력
재검증이나 기본 Voice 정책 변경은 하지 않는다.

## 유지하는 안전 경계와 바뀌는 평가 경로

- 실제 explicit memory write 뒤의 `acknowledge_memory`는 기존대로 생성 없이 응답한다.
- 기존 act 분류/출력 예산과 Core의 claim gate, 저장·검색·maintenance, Worker opt-in/권한은 유지한다.
- 후보 자체는 모델/외부 Worker/저장소를 호출하지 않는다. 일반 응답은 한 번 생성하고,
  기존 안전 검토가 실패하면 repair가 추가될 수 있다. 무조건 1회라고 주장하지 않는다.
- 문맥 후보는 행동 label을 `contextual`로 반환한다. 따라서 `listen`, `state_view`,
  `self_reciprocate` 등에만 적용되던 기존 **말투/행동별** repair 조건은 이 후보에서 선택되지
  않는다. 이 변화는 숨기지 않고 T2.2의 행동 충족/repair율/과잉 차단 비교에 포함한다.
  label과 무관한 `ResponseClaimGate`는 그대로 실행되며 안전 실패를 우회하지 않는다.
- 잘못된 정책 이름/실행 오류는 명시적으로 실패하며 legacy/fake로 몰래 전환하지 않는다.
- 기본은 legacy다. CLI의 후보 선택은 `Experimental dialogue policy`로 표시한다.

## 평가 준비와 한계

후보 구현 전에 `configs/dialogue/evaluation.py` v1의 10장면을 등록했다. development 5개와
reserved 5개는 공감+해결, 농담→정정, 자기 의견, 기억 불확실성, 종료를 각각 포함한다.
reserved는 개발용 모델 입력·행동 테스트에서 제외하지만 작성자가 본 장면이므로 독립적인
blind benchmark라고 부르지 않는다. 실제 모델로 평가한 적도 아직 없다.

기존 6종 모델 요청 기준선은 변경하지 않고 보존한다. 새 offline 검사는 정책 선택과 전달,
독립 비교 축, 불변 기본값, 저장·오류·claim gate와 Worker opt-in을 검증한다.
이 통과는 공감/농담/관계 품질 성공률이 아니다. 실제 비교는 [T2 평가 계획](../text-dialogue-evaluation.md)과
다음 T2.2에서 수행하고 후보 기본값 승격은 별도 결정한다.
