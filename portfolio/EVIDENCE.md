# Evidence Register

| Claim | Value | Source file/log | Reproducible | Public safe | Note |
| --- | --- | --- | --- | --- | --- |
| `caption_overlap_fix` | 5,669 → 3,547 characters; 37.4% removed | `docs/adr/0003-reference-transcription-strategy.md:84-112`; `src/companion/reference_subtitle_probe.py:540-564` | 부분: private caption 필요 | Yes | 37.4% = `(5,669 - 3,547) / 5,669`. 전체 dataset duplicate rate가 아니라 구 parser가 센 문자 중 rolling overlap으로 판정된 비중. |
| `question_length_change` | 6.67 → 8.88 words | `docs/content-holdout-evaluation.md:58-69` | 부분: private pairs 필요 | Yes | short-gap bridge 뒤 관찰값. pair 수도 동시에 변했으므로 독립적 인과 효과가 아님. |
| `round_1_size` | total 11 / held-out 6 | `docs/diarization.md:161-169`; `docs/content-holdout-evaluation.md:31-38`; `experiments/content_holdout_eval.py:35-45` | 부분: private pairs와 local model 필요 | Yes | 0.5 time-ordered split에서 11개 중 뒤 6개가 held-out. |
| `round_2_size` | total 40 / held-out 20 | `docs/content-holdout-evaluation.md:40-52`; `experiments/content_holdout_eval.py:35-45` | 부분: private pairs와 local model 필요 | Yes | source 범위를 확장한 두 번째 기록. |
| `round_3_size` | total 42 / held-out 21 | `docs/content-holdout-evaluation.md:58-75` | 부분: private pairs와 local model 필요 | Yes | short-gap bridge 뒤 pair/held-out 수. 질문 길이도 동시에 변함. |
| `heldout_position_evolution` | 0.021 → 0.105 → 0.157 | `docs/content-holdout-evaluation.md:31-76` | 부분: private pairs와 local model 필요 | Yes | `reference_broadcast` profile의 recorded-answer wording similarity 위치. accuracy나 제품 대화 품질 점수가 아님. |
| `chance_relative_formula` | `(mean_similarity - chance_similarity) / (1 - chance_similarity)` | `src/companion/content_evaluation.py:54-65`; `docs/content-holdout-evaluation.md:18-27` | Yes: 공개 코드 | Yes | chance baseline=서로 다른 recorded answers의 유사도, ceiling=exact match 1.0. |
| `time_ordered_split` | 앞부분 train / 뒷부분 held-out, ratio 0.5 | `experiments/content_holdout_eval.py:35-45`; `docs/content-holdout-evaluation.md:100-108` | Yes: 공개 코드 | Yes | 무작위 분할이 아니라 recorded order를 유지. |
| `exact_pair_dedup` | exact `(prompt, response)` duplicate drop | `experiments/merge_pairs.py:1-43` | Yes: 공개 코드 | Yes | chunk 재실행으로 같은 exchange가 중복 집계되는 것을 방지. semantic dedup 주장이 아님. |
| `short_gap_bridge` | same speaker + unassigned gap ≤ 1.0 s | `src/companion/diarization.py:300-353`; `docs/content-holdout-evaluation.md:58-69` | Yes: 공개 코드; 실제 값은 private data 필요 | Yes | 다른 화자나 긴 침묵은 합치지 않음. |
| `provenance_schema` | SHA-256, byte size, format/container, codec, sample rate, bitrate, relative path | `docs/reference-audio-ingest.md:39-45`; `src/companion/reference_audio_ingest.py:59-74` | Yes: 공개 schema | Yes | 실제 field value, URI와 media filename은 package에 포함하지 않음. |
| `private_public_boundary` | source, media, transcript private; schema, code, sanitized aggregate public | `docs/reference-data-storage.md:3-7`; `docs/reference-data-storage.md:81-105`; `docs/reference-audio-ingest.md:73-77`; `experiments/content_holdout_eval.py:1-14` | Yes: 공개 계약 | Yes | Public repo ≠ private source storage. |
| `raw_evaluation_record` | `NOT_AVAILABLE` in public repository | `docs/reference-data-storage.md:51-58`; `docs/text-first-records-handoff.md:28-35` | No: intentionally private | Yes | 지정된 `docs/evaluations/`와 `data/evaluations/`에는 이 historical held-out의 원시 record가 없음. 공개 문서와 계산 코드가 가장 가까운 공개 source of truth. |

## Interpretation boundary

`position_above_chance`는 recorded answer의 특정 wording에 얼마나 가까운지 나타낸다. 질문마다 적절한 답이 하나뿐이라는 뜻이 아니므로 낮은 값은 곧 나쁜 답이 아니다. 3차 변화는 질문 bridge, pair count, held-out count가 함께 바뀐 관찰이며 단일 원인의 A/B 실험이 아니다.

## Pipeline source map

| Stage | Evidence |
| --- | --- |
| Private Registry | `docs/reference-data-storage.md:81-105` |
| Metadata Preflight | `docs/reference-source-probe.md:12-35` |
| Subtitle / Audio | `docs/reference-subtitle-quality.md:60-66`; `docs/reference-audio-ingest.md:12-45` |
| STT | `docs/adr/0003-reference-transcription-strategy.md:38-45`; `docs/adr/0003-reference-transcription-strategy.md:163-168` |
| Sortformer (diarization) | `docs/diarization.md:19-48`; `docs/diarization.md:109-117` |
| Pair Construction | `docs/diarization.md:150-169`; `experiments/reference_exchange_probe.py:106-133` |
| Exact Dedup | `experiments/merge_pairs.py:1-43` |
| Short-gap Bridge | `src/companion/diarization.py:300-353` |
| Time-ordered Held-out | `experiments/content_holdout_eval.py:35-45` |
| LLM Evaluation | `experiments/content_holdout_eval.py:69-90`; `src/companion/content_evaluation.py:90-137` |
| Failure Analysis | `docs/content-holdout-evaluation.md:11-27`; `docs/content-holdout-evaluation.md:58-98`; `docs/adr/0004-training-decision.md:128-163` |

도식은 구현·평가가 개선된 역사적 evidence flow다. 현재 `reference_exchange_probe.py`의 한 번의 실행에서는 `bridge_short_gaps()`가 `build_exchanges()`보다 먼저 호출된다. 따라서 그림의 “Pair Construction → Exact Dedup → Short-gap Bridge”는 round 간 개선 순서이며 현재 단일-run call graph를 뜻하지 않는다.
