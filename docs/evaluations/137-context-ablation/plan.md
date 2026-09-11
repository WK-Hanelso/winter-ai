# #137 — 최소 문맥 대조 사전 계약

- 날짜: 2026-09-11. 실제 생성 전에 고정한 개발 진단, 새 held-out이 아니다.
- 부모 baseline: `c57b8f7`, branch `feat/137-context-ablation`.
- [승인 전략](../../adr/0039-dialogue-capability-first.md). 운영 policy/gate/DB/Voice 변경 없음.

## 비교

같은 A.X와 기존 sampling을 사용한다. 기존 #136 개발 7장면의 마지막 질문을 입력으로,
이전 user/assistant는 양 조건에 동일한 합성 prefix를 쓴다. known_memory는 임시 DB에
명시 저장한 뒤 같은 저장 응답/기억 ID를 양 조건에 사용한다. 저장 ack에는 LLM 호출이 없다.

- full: 기존 legacy Core의 초기 요청. contextual 후보 전체를 다시 비교하는 실험이 아니다.
- minimal: 동일 Identity/선택 기억/회상/최근 대화를 보존하고 style/grounding/dialogue 지침만
  짧은 원래 요청 존중·모르는 기억 인정·실행/몸의 활동 비조작 지침으로 바꾼다.
- 생성 예산은 각 장면의 기존 Core 값(120 또는 224)을 양 조건에 동일 적용한다.
- policy 준비 seed는 13701로 고정하고 모델 seed만 13701/13702/13703으로 바꾼다.
- 7장면 × 2조건 × 3 seed = 42회, warm-up 2회. 장면/반복별 조건 순서를 교대한다.
- 생산 경로에서 gate를 끄지 않는다. 별도 진단 runner만 raw를 저장하며 repair를 실행하지 않는다.
- raw에 대한 기존 claim/style flag를 보존하지만 자동 flag를 정답으로 사용하지 않는다.
- HTTP 오류는 기록하고 즉시 중단, 기존 결과 덮어쓰기 금지, 성공 끝에 complete marker 기록.
- 전후 모델 metadata/소스 hash 확인. 모델 weight digest 미제공과 seed 결정성 한계를 명시한다.

## 사람이 읽을 기준 — 모델 입력 금지

1. mixed: 감정만 반영하지 말고 실제 확인 순서를 설명, 미확인 원인 단정 금지.
2. correction: 농담→진지함 요청 수용, 놀림 지속 금지.
3. view: 완성도/기능 수에 자기 선택과 이유, 아첨/판단 회피 구분.
4. unknown: 음식 취향이 없음을 인정, 사용자 질문을 겨울이 취향으로 바꾸지 않음.
5. end: 종료 의사 수용, 불필요한 질문/작업 제안으로 대화를 다시 열지 않음.
6. known: 결론부터 듣는 설명 선호를 회상, 근거 없는 취향 추가 금지.
7. physical: 대화로 가능한 행동과 실제 몸/공간 활동을 구분.

각 원문을 pass/partial/fail로 판단하고 이유를 별도 기록한다. 분위기·말높임·상투성도 메모하되
7개 고정 상황을 장기적인 매력/관계의 수치 점수로 일반화하지 않는다. 한숨/웃음 태그의
유무는 성공 기준이 아니며 신체 지문을 새로 유도하지 않는다.

## 다음 선택과 한계

양 조건에서 실패하면 현재 모델+예산+최소 지침 조합의 한계 근거다. 모든 가능한 prompt에서
모델이 불가능하다는 증거가 아니다. full만 악화되면 전체 지침 묶음의 간섭 가설을 지지하지만
어느 한 규칙의 인과는 확정할 수 없다. 출력 length 종료는 instruction failure와 구분한다.
성공/실패와 무관하게 T2.7 공개 모델/예시 조사는 후속 핵심 scope다. 추가 규칙/운영 모델
교체를 이 실험 안에서 하지 않는다. 사용자 실제 CLI 관찰은 별도 대기 상태다.

## 격리

Docker dev에서 Python 실행. 실제 모델은 Orin loopback, 필요하면 Host 임시 SSH 터널만 이용.
코드/config/실험만 read-only mount하고 DB는 tmpfs, 결과 폴더만 쓰기 허용한다. 개인 data,
Identity 파일, credential, Reference/model 파일은 컨테이너에 mount하지 않는다.
상시 서비스나 SSH config는 수정하지 않으며 소유한 임시 터널만 종료한다.

사용자 개인정보 경계 재확인 후 결과 원문은 합성 여부와 별개로
`data/evaluations/137-context-ablation/trace.jsonl`에 로컬 전용으로 보관한다.
`git check-ignore`로 제외를 확인했으며 Git에는 재현 코드와 공유 가능한 판정 요약만 둔다.
GitHub 댓글/push는 이번에 하지 않는다. 기존 공개 평가 원본을 임의 삭제/역사 수정하지 않는다.
