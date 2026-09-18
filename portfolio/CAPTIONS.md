# Portfolio Asset Captions

## 01_data_quality_fix.png / 01_data_quality_fix.svg

Notion 위치: Featured Work 4 상단, Problem 다음

Caption: 자동 자막의 rolling overlap을 token 경계에서 제거하자 구 parser가 센 caption text는 5,669자에서 3,547자로 교정됐다. 37.4%는 전체 데이터셋 중복률이 아니라 **구 parser가 집계한 문자 중 rolling overlap으로 판정되어 제거된 비중**이다. short-gap bridge 전후에는 질문 평균 길이 6.67→8.88단어와 함께 pair 40→42, held-out 20→21이 동시에 변했다.

Claim boundary: 문자 수는 caption 개수나 데이터셋 duplicate rate가 아니다. Panel B는 여러 값이 함께 변한 before/after snapshot이며 질문 길이 변화의 단일 원인 효과를 증명하지 않는다.

## 02_winter_pipeline.svg / 02_winter_pipeline.png

Notion 위치: Decision / What I Built 사이

Caption: 비공개 source의 registry와 media/transcript는 private storage에 남기고, 공개 저장소에는 schema·검증 코드·sanitize된 집계와 failure analysis만 남겼다. 파이프라인은 source 확인부터 time-ordered held-out과 실패 원인 분리까지의 evidence flow를 보여준다.

Claim boundary: 단계 순서는 구축·개선의 역사적 흐름이다. 현재 단일 `reference_exchange_probe.py` 호출에서는 short-gap bridge가 pair construction보다 먼저 실행된다. 그림은 production serving architecture나 대규모 데이터 파이프라인을 주장하지 않는다.

| Stage | Module / document evidence |
| --- | --- |
| Private Registry | `docs/reference-data-storage.md`; `src/companion/reference_storage.py` |
| Metadata Preflight | `docs/reference-source-probe.md`; `src/companion/reference_source_probe.py` |
| Subtitle / Audio | `docs/reference-subtitle-quality.md`; `src/companion/reference_subtitle_probe.py`; `docs/reference-audio-ingest.md`; `src/companion/reference_audio_ingest.py` |
| STT | `docs/adr/0003-reference-transcription-strategy.md`; `src/companion/reference_transcription_probe.py` |
| Sortformer | `docs/diarization.md`; diarization probe adapter 기록 |
| Pair Construction | `experiments/reference_exchange_probe.py`; `src/companion/diarization.py::build_exchanges` |
| Exact Dedup | `experiments/merge_pairs.py::load_pairs` |
| Short-gap Bridge | `src/companion/diarization.py::bridge_short_gaps` |
| Time-ordered Held-out | `experiments/content_holdout_eval.py::split_pairs` |
| LLM Evaluation | `experiments/content_holdout_eval.py`; `src/companion/content_evaluation.py` |
| Failure Analysis | `docs/content-holdout-evaluation.md`; `docs/adr/0004-training-decision.md` |

## 03_heldout_evolution.png / 03_heldout_evolution.svg

Notion 위치: Evidence 핵심 차트

Caption: 데이터는 total/held-out 11/6 → 40/20 → 42/21로 확장·교정됐고, `reference_broadcast`의 chance-relative position은 0.021 → 0.105 → 0.157로 움직였다. 값은 chance baseline과 exact-match 1.0 사이에서 recorded answer wording에 가까워진 상대 위치다.

Claim boundary: 축은 accuracy가 아니다. 자연스러움, 적절성, 제품 대화 품질을 측정하지 않으며, Round 3은 input 구성과 표본 크기가 함께 달라진 비통제 비교다.

## 04_provenance.svg / 04_provenance.png

Notion 위치: Scope / Limitation 또는 Security / Privacy 블록

Caption: ingest record는 SHA-256, byte size, format/container, codec, sample rate, bitrate, managed relative path를 보존한다. 공개 저장소에는 이 field schema와 validation만 두고 실제 값·URI·media filename·원문은 private storage에 유지한다.

Claim boundary: 그림은 schema 존재를 보여주며 실제 private record 값이나 source의 공개·재배포 가능성을 뜻하지 않는다. Public repo와 private source storage는 동일한 저장소가 아니다.
