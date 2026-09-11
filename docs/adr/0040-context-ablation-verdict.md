# ADR 0040 — 최소 문맥 진단 후 모델/예시 조사로 이동

- 날짜: 2026-09-11
- 상태: #137 로컬 실행 범위 완료, 운영 activation 없음, GitHub OPEN/동기화 보류.
- 근거: [사전 계약/42회 결과](../evaluations/137-context-ablation/README.md),
  [승인 전략](0039-dialogue-capability-first.md), 이전 [#136](0038-postfix-text-evaluation-verdict.md).

## 결정

최소 지침에서도 없는 기억·의견 회피가 남았다. 전체 legacy 지침은 일부 장면에서 직접성에
도움이 되었고 검사 오탐도 별도로 관찰됐다. 전체 gate 제거, regex 확대 또는 모델 교체를
이 결과만으로 실행하지 않는다. 기존 legacy 유지가 좋은 제품 품질을 승인하는 것은 아니다.

다음 작업은 T2.7 공개 모델/예시 조사다. 원문 회상·자기 관점·상투성·형체 없는 표현을
모델/예시 선택의 축으로 삼고, 기존 실패한 모델의 동일 조건 반복은 피한다. 조사 뒤 후보
실측과 CLI 연결을 별도 Issue로 결정한다. 학습/Voice/DB migration을 선행 강제하지 않는다.

## 한계와 보존

3 seed·고정 합성 prefix의 7개 개발 장면이며 새 held-out, 장기 관계, 실제 사용자 경험의
증거가 아니다. 최소 지침은 여러 블록을 함께 바꿨으므로 특정 한 지침의 인과를 분리하지
못했다. 기존 출력 예산과 prefix cache도 시간/length에 영향을 준다. 운영 repair는 실행하지 않았다.

원출력과 개별 검토는 `data/evaluations/137-context-ablation/`에 Git 제외로 보관한다.
Git에는 재현 코드·공유 요약만 남긴다. 사용자 개인정보 경계 확인 뒤 이번 GitHub 댓글/push는
하지 않으며 #137을 원격에서 닫았다고 말하지 않는다. 후속 Issue도 아직 등록하지 않았다.
`project-state.toml`은 #137로 유지하고, 후속 scope/Issue 동기화 전 새 구현에 착수하지 않는다.
