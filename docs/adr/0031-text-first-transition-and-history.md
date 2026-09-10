# ADR-0031: Text-first 전환과 기존 기록 계승

- Status: Accepted (사용자의 2026-09-10 T0 착수 승인)
- Issues: #127 (Epic), #128 (T0), #129 (첫 코드 작업)
- Supersedes: 기존 Voice-first 개발 순서와 #126 phone canary의 최우선 지위만 대체한다.
  과거 ADR 원문·품질 실패·privacy·승인 경계를 폐기하지 않는다.

## 맥락

사용자는 일관된 겨울이와 기억, 외부 Agent 활용, 관계가 느껴지는 대화를 텍스트에서 먼저
경험하기 원하며 Voice를 후순위로 옮겼다. 또한 기존 기록을 무시하는 재출발을 거부했다.
이미 구현한 기반은 재사용하지만 실행 장치의 존재를 제품 경험의 완성으로 표현하지 않는다.

## 결정

1. 현재 phase를 `text_first_foundation`으로 바꾸고 T0 #128에서 문서와 범위를 동기화한다.
   T0 완료 시 다음 active scope는 T1.1 #129로 넘기며 코드 착수와 구분한다.
2. 새 Text-first는 contract로 등록하며 기존 기능 maturity는 유지한다.
3. baseline `0c4f55775a3666a27a06e17361189a5e3a354169`와 #126 branch를 보존하고 후손
   branch에서 일한다. 기존 history를 rewrite하거나 원격 main에 자동 병합하지 않는다.
4. 기존 대화/기억 DB·Identity·Reference는 그대로 사용한다. T0는 migration/backup/삭제를
   수행하지 않는다. 이후 데이터 변경에는 일관된 backup·복사본 검증·복구 방법을 먼저 둔다.
5. [기록 인계표](../text-first-records-handoff.md)가 원문 근거와 후속 작업을 연결한다.
   새로운 요약은 탐색용이며 원본을 대체하지 않는다.
6. #126은 open/partial로 보류하고 결과·실패·재사용할 job/inbox/scheduler/gateway를 남긴다.
7. T1.1 #129에서는 bootstrap만 추출한다. 모델·DB schema·대화 정책을 동시에 바꾸지 않는다.
8. Voice 고도화와 실제 phone 검증, Level 3 전체 자기 코드 개선은 후순위다. 기존 Voice
   코드/데이터와 호환 회귀는 보존하며 실행 중인 서비스를 T0에서 변경하지 않는다.

## 결과

개발자는 기록을 모두 다시 읽는 대신 현재 결정→인계표→관련 원문을 따라간다. 데이터의
원본 보존, 검색 정확성, 모델의 회상은 별도 목표로 다룬다. 기록이 보존됐다는 이유로
접근하지 못한 Claude/Codex 세션이나 과거 모든 정보가 겨울이 Memory에 들어 있다고 말하지 않는다.

## 검증

T0에서는 project-check, 문서 링크/상태/diff와 Git baseline 조상 관계를 확인한다.
코드·모델·runtime의 품질 향상이나 오늘의 서비스 health를 검증한 작업은 아니다.
