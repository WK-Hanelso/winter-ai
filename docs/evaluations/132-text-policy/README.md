# #132 실제 텍스트 정책 비교 — 후보 승격 보류

2026-09-10. **실제 평가 실행 범위는 완료했지만, 대화 품질 gate는 통과하지 못했다.**
기본값은 `legacy` 그대로다. 아래 내용 판정은 Codex의 출력 검토이며 천우의 사용자 관찰이나
독립된 사람 평가를 대신하지 않는다. 사람 검토 상태는 pending으로 남긴다.

## 조건과 기록

- 실제 서버: 사용자 소유 Orin의 `A.X-4.0-Light-Q4_K_M.gguf`, 약 7.26B parameters,
  llama.cpp `b1-8e7f22b`, context 4096, server slots 4. 모델/서비스 설정 변경 없음.
- Core/policy baseline은 `c187dc8`이다. runner/policy/scene/profile SHA-256은 각 manifest에
  기록했고 주 분석 네 파일의 hash가 현재 소스와 모두 일치함을 확인했다.
- temperature 0.7 / top_p 0.8 / top_k 20 / presence_penalty 1.0, 요청 seed와 style seed 13201.
  짧은 warm-up 2회 응답은 각 배치에서 일치했다. 전체 응답의 완전 결정성은 보장하지 않는다.
  weight digest는 서버에 없어 filename/size/architecture만 기록했다.
- `synthetic-t2-v1` 정체성, reference_conversation 말투 프로필, scene/정책마다 임시 SQLite.
  시작 기억은 비어 있다. 실제 개인 기억의 검색 품질/유실이나 관계 이력을 시험한 것은 아니다.
- Docker는 src/configs/experiments와 결과 경로만 mount하고 root filesystem은 read-only다.
  개인 DB·`.env`·Human Reference·credential·weight를 mount하지 않고 Worker를 연결하지 않았다.
- 기본 `:18080` 터널은 없었지만 Orin 모델 health는 정상이라 평가 전용 loopback `:18082`
  SSH 터널을 사용했다. 평가 뒤 해당 프로세스만 종료했다. 기존 `:18080`이나 Winter 서비스를
  복구/변경하지 않았으므로 일반 CLI 연결 문제는 별도로 남는다.

주 분석: development 5 + reserved 5장면 × 4정책 × (고정 문맥 마지막 1턴 + 독립 rollout 2턴)
= **120턴, 133회 모델 호출**, HTTP 실행 실패 0. warm-up은 별도다.
고정 문맥의 앞선 assistant 발화는 모든 정책에서 `응, 듣고 있어.`로 같다. rollout은 각 정책의
실제 앞선 답변을 사용하므로 후속 문맥 차이도 정책별 결과의 일부다.

사용한 파일:

- [development 고정 문맥](development-fixed.jsonl)
- [development 순차 rollout](development-rollout-sequential.jsonl)
- [reserved 순차 고정 문맥](reserved-fixed-sequential.jsonl)
- [reserved rollout](reserved-rollout.jsonl)

초기 [development rollout](development-rollout.jsonl)과 [reserved 고정 문맥](reserved-fixed.jsonl)은
배치 전환 때 일부 실행이 겹쳤다. 총 60턴 원본을 보존하되 주 분석에서 제외하고 순차로
재실행했다. 정책/prompt는 중간에 변경하지 않았다. 제외된 표본을 추가 성공 표본으로 세지 않는다.

전체 요청·모델 원문·repair 원문·최종 응답·자동 claim 검사·latency·finish reason·usage를 JSONL에
기록했다. [읽기 쉬운 전사](transcript.md)와 [수치 원본](metrics.json)을 함께 제공한다.

## 시간과 자동 검사 — 품질 점수가 아님

| 정책 | 턴 | 중앙값 | p95 | 최대 | repair 발생 턴 | claim flag 발생 턴 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| legacy | 30 | 0.89초 | 2.74초 | 4.90초 | 3 | 1 |
| contextual | 30 | 1.50초 | 5.24초 | 7.52초 | 5 | 5 |
| contextual-moves-only | 30 | 1.26초 | 4.19초 | 4.47초 | 2 | 2 |
| stable-register-only | 30 | 1.02초 | 2.36초 | 3.12초 | 3 | 3 |

Core 응답 완료까지의 시간이다. non-streaming 실행이므로 첫 token/첫 음성 지연은 측정하지
않았다. 작은 혼합 표본의 기술통계이며 실제 사용 SLA나 유의한 품질 차이를 뜻하지 않는다.
주 분석의 모든 모델 호출의 finish_reason은 stop이었다. 따라서 아래 목록 잘림은 서버 token 한도
종료가 아니라 기존 Core 후처리 문제다. claim flag는 참/거짓을 보장하지 않는다.

## 내용 검토와 실패 근거

1. **복합 요청:** legacy는 `dev_mixed` rollout에서 최종 답이 “확인할 부분이 몇 가지 있어.”로
   끝났다. contextual/moves-only는 확인 절차를 제공해 실용적 요청 충족이 개선됐다. 그러나
   공감과 설명의 균형·반복·형식이 자연스럽다는 결론까지 내릴 수 없다.
2. **후처리 잘림:** `reserved_mixed` rollout moves-only의 모델 원문은 업무 목록/우선순위/
   역할/일정/점검의 다섯 단계를 썼지만, 최종 응답은 목록의 `2.`에서 끝났다. `split_sentences`
   와 문장 개수 제한이 목록 번호도 문장으로 센 결과다. 원문/최종을 분리하지 않았다면
   모델의 설명 능력 실패로만 오판했을 것이다. 이번 평가에서 코드는 수정하지 않았다.
3. **자기 의견:** `dev_view` fixed에서 legacy는 완성도 선택과 이유를 말했지만 contextual은
   “완성도랑 기능 수 모두 중요하다고 생각해. 하지만 상황에 따라 다를 수 있어.”로 회피했다.
   후보가 모든 장면에서 기존 정책보다 좋지는 않다.
4. **모르는 기억:** `dev_memory` fixed legacy는 정확히 기억 못 한다면서 “네가 자주 언급했던
   음식이 있어”라고 근거를 만들었다. contextual은 repair 뒤 사용자가 아닌 자기 음식 이야기로
   주어가 바뀌었다. moves-only도 근거 없는 언급을 남겼다. rollout contextual의 “그건 지금
   내 기억 근거에는 없어.” 같은 성공도 있지만 일관적이지 않다. **빈 합성 기억 테스트이므로
   실제 DB의 기억이 사라졌다는 증거는 아니다.**
5. **검사 오탐/누락:** contextual의 “너는 자주 먹는 음식이 뭐야?”라는 질문을 outside_event로
   차단한 뒤 응답이 악화됐다. 반대로 legacy의 “자주 언급했던 음식”은 검사에서 통과했다.
   repair 뒤 문장 제한 때문에 내용 없는 “알겠어. 그럼 다시 말해볼게.”만 남는 사례도 있었다.
   안전 gate를 제거하거나 이 결과를 안전 성공으로 표현하지 않는다.
6. **분위기 정정:** 개발 장면에서는 대체로 진지한 대화 전환을 받아들였다. 예약 rollout의
   legacy는 경계 요청에 “그냥 기분 풀어줘!”라고 반응했고, contextual도 “농담이었어”라는
   자기 해명으로 끝났다. 모든 후보가 관계적 자연스러움을 확보한 것은 아니다.
7. **종료와 말높임:** 평가의 마지막 종료 요청은 대체로 질문 없이 받아들였다. 하지만 rollout
   도중 일반 질문/반복이 남았고, legacy는 기억 질문에서 “죄송하지만”과 반말을 섞었다.
   말높임 추첨 제거만으로 기억·내용 문제를 해결하지 못했다.

## 결정과 후속

- **기본값 legacy 유지.** 후보는 명시적 실험으로만 남기고 품질/안전 gate를 승격하지 않는다.
- 의미 해석/생성, Core의 길이 후처리, claim gate의 오탐/누락은 다른 실패 축이다. 문장 잘림과
  repair 품질의 회귀 사례는 이 원본에서 후속 Issue로 가져가며 장면별 regex 예외를 추가하지 않는다.
- 당초 계획의 T3.1 bounded history 조회는 독립적인 후속 리팩터링이다. 그것만으로 이번의
  빈 기억 환각이나 어색한 말투가 고쳐진다고 주장하지 않는다. 실제 관계·장기 기억 UX는 계속 미검증이다.
- 천우의 후보 사용은 비차단 관찰이다. 현재 Host 기본 터널이 끊겨 있으므로, 실제 개인 CLI
  시험은 터널 복구 여부를 먼저 별도 결정해야 한다. 평가용 연결을 영구 운영 연결로 바꾸지 않았다.

## 도구 검증

Docker offline **898 passed (40.69초)**, Ruff 전체 통과, mypy **181 source files** 통과.
새 runner 계약 9개는 loopback-only/비밀 URL 거부, payload 보존, fixture DB 분리, 오류 기록과
배치 중단을 확인한다. 실제 모델 성공 120턴과 offline 898개는 별개의 수치다.
