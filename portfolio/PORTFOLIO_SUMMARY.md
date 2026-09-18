# Winter — 로컬 LLM 데이터 구축과 held-out 평가

## One-line

실제 대화·음성 데이터에 provenance, rolling-overlap 제거, exact dedup, 시간순 held-out을 적용하고, 낮은 평가값을 모델 교체보다 입력 품질 문제로 먼저 분해했다.

## Problem

대화형 음성 원본은 자막의 rolling overlap, 화자 혼합, 숨 구간에서 잘린 질문 때문에 그대로 학습·평가 데이터로 쓸 수 없었다. 초기 parser는 겹친 caption 문자를 중복 집계해 STT 품질 판단까지 왜곡했다. 또한 recorded answer와의 유사도가 낮아도 모델 문제인지 질문 구성 문제인지 분리되지 않았다. 비공개 원본을 공개 저장소에 노출하지 않으면서도 결과의 provenance와 재현 계약을 남겨야 했다.

## Decision

- 원본과 파생 대화·음성은 private storage에 두고, 공개 저장소에는 schema·검증 코드·sanitize된 집계만 남겼다.
- 자동 자막은 cue 경계의 token overlap만 제거하고, 실제 발화 안의 반복은 보존했다.
- 임의 분할 대신 기록 순서의 뒤 절반을 held-out으로 두고, 낮은 결과가 나오면 모델 변경 전에 pair 생성과 질문 잘림을 점검했다.
- 평가는 “정답률”이 아니라 chance baseline과 exact-match 1.0 사이의 recorded-answer wording similarity로 한정했다.

## What I Built

- Private Registry → metadata preflight → subtitle/audio ingest → STT → Sortformer 화자 분리 → pair construction → exact dedup → short-gap bridge → time-ordered held-out → local LLM evaluation → failure analysis 흐름
- SHA-256, byte size, format/container, codec, sample rate, bitrate, relative path를 보존하는 ingest provenance record
- 같은 화자 사이의 짧은 미배정 구간을 이어 질문 조각화를 줄이는 bridge와, prompt/response 완전 일치 기준의 pair dedup
- private pair text를 출력하지 않고 count와 aggregate score만 보고하는 평가 경계

## Evidence

- 구 parser가 센 5,669자 중 rolling overlap을 제거해 3,547자: 제거분은 구 parser 집계 문자의 37.4%이며 전체 데이터셋 중복률이 아니다.
- 질문 평균 길이 6.67 → 8.88단어. 같은 변경에서 전체 pair도 40 → 42, held-out도 20 → 21로 바뀌었다.
- 데이터 진화: 전체/held-out 11/6 → 40/20 → 42/21.
- chance-relative position: 0.021 → 0.105 → 0.157. 이는 제품 대화 품질이나 accuracy가 아니다.

## Failure / Debugging

첫 실패는 rolling caption의 “직전 cue 전체 prefix”만 제거한 parser였다. 실제 서비스 자막은 직전 cue의 꼬리를 다음 cue의 머리에 반복했고, 그 결과 caption 문자가 약 37.4% 과대 집계되어 Whisper가 내용을 절반가량 누락한다는 잘못된 결론까지 만들었다. 대안은 원본을 버리거나 더 큰 STT 모델로 바꾸는 것이었지만, token 단위 최대 suffix/prefix overlap 제거를 먼저 적용해 측정 도구 자체를 교정했다.

두 번째 실패는 1차 held-out position 0.021을 바로 모델 한계로 읽을 위험이었다. 6개 held-out, 잘린 질문, 문맥 부재가 함께 있었기 때문에 모델 교체·학습 대신 pair 확장과 short-gap bridge를 우선했다. 그 뒤 0.105, 0.157이 관찰됐지만 exact-match 1.0과는 여전히 멀고, 서로 다른 적절한 답을 인정하지 못하는 metric 한계도 남는다.

## Result

모델을 바꾸지 않고도 평가 입력의 결함이 수치에 미친 영향을 분리했다. 최종 공개 기록은 전체 42개·held-out 21개와 chance-relative position 0.157을 남기며, 낮은 결과를 숨기지 않고 다음 판단을 “추가 학습”이 아니라 문맥 제공과 register별 profile 검토로 연결한다.

## Scope / Limitation

- 이번 package는 기존 기록을 시각화했으며 새 실험·서비스 실행·모델 학습을 하지 않았다.
- 원시 평가 JSON과 대화·음성·자막은 private storage에 있어 공개 package에서 `NOT_AVAILABLE`이다.
- 0.157은 한 recorded answer와의 문자열 유사도 위치이며, 자연스러움·적절성·정답률의 증거가 아니다.
- 3차에서는 질문 길이, pair 수, held-out 수가 동시에 바뀌어 단일 변수의 인과 효과로 해석할 수 없다.
- Human Reference는 최종 Winter character가 아니며, 이 결과는 production 또는 large-scale 성능 주장이 아니다.

## Portfolio Assets

- `assets/01_data_quality_fix.png` — rolling-overlap 교정과 question/pair 구성 변화
- `assets/02_winter_pipeline.svg` — private source storage와 public evidence 경계를 포함한 구축·평가 흐름
- `assets/03_heldout_evolution.png` — 세 round의 total/held-out과 chance-relative position
- `assets/04_provenance.svg` — 공개하지 않는 실제 값과 공개 schema·검증 코드의 분리

상세 claim, 원천 위치, 재현성 경계는 `EVIDENCE.md`와 `evidence.json`에 있다.
