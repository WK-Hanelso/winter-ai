# ADR 0036 — 문장 수 절단 대신 전체 응답 검토·보존

- 날짜: 2026-09-10
- 상태: 채택 (#134의 코드 계약; Text-first 전체 품질 승격 아님)
- 선행: [ADR 0035](0035-text-policy-evaluation-verdict.md), #132 실제 출력

## 문제

Core가 초안, repair, 최종 저장에서 `split_sentences(... )[:limit]`을 적용했다.
`1.`도 문장으로 세어 목록이 `2.`에서 끝났고, Director의 listen 제한 1은 설명을
서론만 남겼다. 실제 #132 두 사례의 서버 finish reason은 모두 `stop`이었다.
스트리밍도 문장 수로 generation을 종료했다. streaming 미지원 모델의 대체 경로는
review 없이 callback을 호출해 최종 저장과 전달 내용이 달라질 수 있었다.

## 결정

- Shared Core는 초안/repair/final의 내부 줄바꿈·목록·코드·문장을 보존한다. 양끝 공백만 제거한다.
- 길이는 기존 prompt의 말투 지침과 ChatRequest max_tokens(일반 120, explain 224)로
  조절한다. 모델/샘플링/Director/prompt/claim 탐지 규칙은 변경하지 않는다.
- DialoguePlan.sentence_limit은 기존 계약 호환용으로 남지만 더는 출력 절단기가 아니다.
  이미 있는 말투 prompt가 생성 길이를 유도한다. 기존 요청 fixture를 다시 만들지 않는다.
- 전체 초안과 전체 repair를 기존 reviewer/claim gate에 넘긴다. safety repair 횟수는
  기존처럼 최대 한 번이다. 위반이 남으면 기존 fallback을 사용하며 최종 gate도 유지한다.
- 일반 stream과 미지원 대체 경로 모두 review → finalize/저장 → callback 순서다.
  기억 저장 알림도 finalize의 최종 결과를 전달한다. stream 오류에는 iterator를 닫고
  미검사 초안이나 성공 assistant 기록을 남기지 않는다.
- Worker의 원문 보존 예외 플래그는 불필요해 제거한다. 기존 권한·출처·결과 metadata와
  별도 접수/status 알림은 유지한다. TTS용 split_sentences 자체는 변경하지 않는다.

## Trade-off와 남은 문제

- 기존보다 긴 답변이 보이며 stream 조기 중단이 없어 생성 시간/첫 callback이 늘 수 있다.
  실제 TTFT/음성 성능 개선을 주장하지 않는다. 이 경로는 안전 검토가 끝나야 전달한다.
- 서버 토큰 한도나 모델 자체가 만든 불완전한 문장은 이 변경으로 복원되지 않는다.
  문장 경계로 성공을 추측하지도 않는다. 다른 ChatModel adapter도 생성 예산/오류 계약을
  준수해야 하며 무한 stream을 허용한다는 뜻이 아니다.
- 전체 repair가 기존보다 길어질 수 있다. 전체 context 예산의 정밀 관리는 T3.3에 남긴다.
- Director의 복합 의도 오분류, reviewer의 listen fallback 문장 선택은 유지한다.
  모든 응답이 원문 그대로라는 뜻이 아니라, 기존 품질/안전 검사를 통과한 내용을 임의의
  문장 개수로 버리지 않는다는 뜻이다.
- 전체 fallback을 재검사하면 기존 body 안내문의 두 번째 문장을 capability로 오탐한다.
  기존 테스트의 기대문도 이 관찰에 맞춰 갱신하되 unsafe 초안 차단 검사는 유지했다.
  탐지 규칙을 약화하거나 fallback을 검사에서 제외하지 않고 다음 claim/repair Issue로 넘긴다.
- 저장 뒤 callback 오류는 이미 저장된 assistant를 되돌리지 않는다. exactly-once 전달은
  이번 범위가 아니다. TTS segmentation은 공백/문장 단위일 뿐 Markdown parser가 아니다.

## 검증

[실제 원문 replay와 전후 비교](../evaluations/134-response-preservation.md)를 따른다.
새 모델 출력/개인 DB/서비스 재배포/사용자 체감 검증과 Docker의 deterministic replay를 구분한다.
rollback은 #134 코드 변경을 되돌리는 것이며 DB schema migration은 없다.
