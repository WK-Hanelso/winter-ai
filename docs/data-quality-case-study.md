# Data Quality Case Study — Rolling Caption Deduplication

## 왜 자막 품질을 측정했는가

Human Reference의 자막은 읽기 좋은 문장이 아니라 머뭇거림과 반복까지 남긴
`raw_transcript`가 되어야 한다. 사람이 편집한 자막과 service automatic caption을
비교해 필러 보존, 문자 수, 유사도와 coverage를 측정한 이유다. 이 통계는 STT 방식과
후속 pair 구성 판단의 입력이 되므로, 먼저 통계 자체를 신뢰할 수 있어야 했다.

## 첫 결과

최초 parser 결과만 보면 사람이 만든 자막은 automatic caption보다 짧았고, local STT는
service caption 문자 수의 약 절반만 산출하는 것처럼 보였다. 이 결과대로라면 사람 자막도
local STT도 `raw_transcript`의 충분한 출발점이 아니라는 결론이었다.

## 이상한 숫자 발견

동일한 source의 두 track에서 문자 수 차이가 예상보다 컸고, STT 비교에서도 service
caption 쪽 분모가 지나치게 컸다. 모델이나 자막 제작 방식의 문제로 결론 내리기 전에
caption cue가 parser를 통과한 형태를 다시 확인했다.

## rolling caption 구조 확인

automatic caption은 매 cue가 완전히 새로운 문장이 아니었다. 직전 cue의 token 꼬리를
다음 cue의 머리에 반복한 뒤 새 단어를 붙이는 rolling 구조가 있었다. 기존 parser는 직전
cue 전체가 다음 cue의 prefix인 경우만 제거해, 부분 tail-prefix overlap을 계속 더하고
있었다.

## parser correction

[`parse_webvtt()`](../src/companion/reference_subtitle_probe.py)는 cue 경계마다 직전 token
suffix와 현재 token prefix의 가장 긴 exact overlap을 찾아 현재 cue의 겹친 prefix만
제거하도록 고쳤다. fuzzy match나 cue를 건너뛴 반복은 지우지 않고 한 cue 내부의 반복도
보존한다. 실제 발화까지 과잉 삭제하지 않기 위한 좁은 규칙이다. 부분 overlap과 내부 반복
보존은
[`test_parse_webvtt_removes_partial_tail_overlap_not_only_whole_prefixes`](../tests/unit/test_reference_subtitle_probe.py)와
인접 회귀 테스트로 고정했다.

## 재측정

한 관측에서 raw cue count는 8,599자였고, 기존 parser 집계는 5,669자, 수정 후 집계는
3,547자였다. 따라서 rolling caption overlap 때문에 **기존 parser가 집계한 5,669자 중
37.4%가 중복으로 판명돼 제거됐다.** 이는 전체 dataset이나 training data의 중복률이
아니며, 수정값을 분모로 한 과대 비율을 뜻하지도 않는다.

## 결론 변경

수정 전에는 사람이 만든 자막이 automatic caption보다 짧다고 판단했다. 재측정 뒤에는
사람 자막 문자 수가 비교한 두 source에서 automatic caption보다 35~42% 많았다. local
STT와 service caption의 문자 수도 비슷한 수준으로 바뀌었다. 다만 사람 자막이 필러의 약
절반을 지운다는 결과는 남았으므로, 사람 자막은 `normalized_transcript`와 정렬 참고자료로
쓰고 `raw_transcript`에는 STT가 필요하다는 판단을 유지했다.

## 얻은 설계 원칙

완벽하거나 극단적인 수치는 곧바로 품질의 증거로 쓰지 않고 parser와 분모부터 확인한다.
dedup은 관측된 rolling 구조에 맞춘 최소 규칙으로 두고, 무엇을 제거하지 않는지도 테스트로
고정한다. dataset statistic을 모델 실험의 입력으로 쓰기 전에 그 statistic 자체가 올바른지
확인해야 한다는 것을 확인했다.
