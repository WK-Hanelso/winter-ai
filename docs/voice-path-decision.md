# Voice path decision gate — 2026-08-14

## 결론

89.4분 데이터로 Seed-VC를 더 학습하는 반복은 여기서 멈춘다. 현재 문제는 데이터
부족보다 **F0를 입력받지 않는 변환 경로가 Chatterbox의 억양을 다시 만드는 구조**에
있다. 학습된 22k 모델은 화자 유사도는 높지만 질문/평서 종결 차이와 문장 내부 억양
폭을 절반 이상 잃는다.

객관 지표의 작업 후보는 Seed-VC의 44k F0 모델과 `auto_f0_adjust=True`를 사용하는
경로였지만, blind 청취에서 거친 소리가 가장 심해 탈락했다. 이번 세 후보는 모두
Reference 유사성과 질문 억양의 합격선에 이르지 못했다. 운영 경로는 전환하지 않고,
상대적으로 가장 나았던 Chatterbox S3Gen Reference 경로를 다음 모델 비교의 기준선으로
남긴다.

## 공정한 비교 조건

- 같은 Chatterbox V3 생성분 6개: 같은 어휘의 평서문/의문문 3쌍
- Chatterbox와 F0 변환의 random seed 고정
- 화자 유사도는 합성에 쓴 `ref-J`가 아니라 held-out `ref-A`~`ref-I`의 평균
  embedding에 대해 측정
- 억양은 source 절대값이 아니라 같은 생성분에서의 보존율로 측정
- 내용은 local Whisper로 재전사
- 원본과 결과는 `review/voice-path-bakeoff/`에만 두며 Git에 넣지 않음

## 결과

| 경로 | held-out 화자 유사도 | 질문/평서 차이 | 억양 폭 | 판정 |
| --- | ---: | ---: | ---: | --- |
| Chatterbox 기본 | 0.131 | 100% | 100% | 자연스러움 기준, 음색 불일치 |
| Chatterbox 직접 Reference | 0.579 | 5% | 44% | 음색은 이동하지만 억양 고정 |
| Chatterbox S3Gen만 Reference | **0.611** | 31% | 42% | 화자 유사도 최고, 억양 문제 유지 |
| Chatterbox T3+S3Gen Reference | 0.608 | -17% | 36% | 질문/평서 방향까지 무너짐 |
| Chatterbox T3 speaker만 Reference | 0.120 | 9% | 88% | 목소리가 이동하지 않음 |
| 현재 89분 Seed-VC | 0.581 | 37% | 43% | 현재 기준선, 억양 문제 유지 |
| Seed-VC F0, pitch 그대로 | 0.383 | 72% | 109% | F0 맞춤 경로에 열세 |
| **Seed-VC F0, target pitch 맞춤** | 0.459 | **122%** | **105%** | 객관 지표의 작업 후보 |

화자 유사도는 절대 품질 점수가 아니라 동일 encoder 안의 순서다. 특히 한 참조만
사용하면 그 참조를 직접 조건으로 쓴 모델이 유리해져서, 최종 표는 보지 않은 아홉
클립의 평균을 기준으로 했다.

## Chatterbox 조건 분리에서 알게 된 것

Chatterbox의 Reference 조건은 하나가 아니다.

1. T3 speaker embedding
2. T3 prompt speech tokens
3. S3Gen acoustic conditioning

speaker embedding만 바꿨을 때 화자 유사도는 0.120으로 기본 0.131과 사실상 같았다.
S3Gen 조건을 Reference로 바꾸면 0.611까지 올라가지만 억양 폭은 42%로 줄었다. 따라서
현재 Reference 음색을 만드는 주된 부분은 S3Gen이고, 그 부분이 억양 고정도 함께
가져온다. 내부 조건을 섞는 것으로 두 문제를 분리한다는 가설은 기각한다.

## 내용과 신호

- 결선 후보들은 6문장 모두 local Whisper에서 의미가 보존됐다.
- F0 맞춤과 현재 모델의 한 문장에서 `거야`가 `거예요`로 전사됐다. 실제 발음 변화인지
  STT 오류인지는 천우의 청취가 필요하다.
- 결선 montage의 peak는 -14.3~-10.8 dBFS로 clipping이 없다.

## 실사용 가능성

F0 서버는 Chatterbox와 RTX 2060 6 GiB에 동시에 상주했다.

| 항목 | 측정 |
| --- | ---: |
| Chatterbox GPU memory | 약 3,192 MiB |
| F0 Seed-VC GPU memory | 약 1,842 MiB |
| 합계 | 약 5,034 MiB |
| F0 cold load | 75.7초 |
| F0 첫 HTTP 변환 | 6.49초 |
| F0 warm 변환 | 3.12초 |
| 현재 89분 모델 warm 변환 | 2.42초 |

상주 후 차이는 같은 1.88초 입력에서 약 0.7초다. cold load는 시작할 때 한 번만
발생하며 요청마다 모델을 다시 띄우면 안 된다.

`compose.vc-f0-server.yaml`이 현재 서버와 같은 `/convert` 계약으로 대체 실행할 수
있는 설정이다. 두 compose는 같은 8091 포트를 사용하므로 동시에 실행하지 않는다.

## 중단 규칙

- 같은 22k Seed-VC에 데이터를 더 넣어 재학습하지 않는다.
- direct/hybrid Chatterbox 조건을 더 조합하지 않는다.
- F0 맞춤이 청취에서 Reference 음색 또는 발음 때문에 탈락할 때만 다음 VC 모델을
  조사한다.
- 청취 전에는 운영 기본값을 바꾸지 않는다.

## 남은 사람 판정

`review/voice-final-listen/`의 세 파일만 듣는다.

- 사람처럼 자연스러운가
- Reference와 충분히 같은 사람인가
- 질문 세 문장이 실제 질문으로 들리는가
- 마지막 `내일 다시 이야기할 거야?`가 `거예요`처럼 변형되어 들리는가

파일 번호와 모델 대응은 blind 답변 전까지 공개하지 않는다. 폴더의 `청취표.md`에
1~5점 표와 짧은 답변 형식을 두었다. 이 판정 외에는 운영 전환에 필요한 사용자 선택이
없으며, 선택된 경로의 서비스 전환과 회귀 검증은 코드 작업으로 처리한다.

## Blind 청취 결과 — 2026-08-14

| Blind 파일 | 실제 경로 | 천우 판정 |
| --- | --- | --- |
| 결선-1 | Seed-VC F0 + target pitch 맞춤 | 3위. 거친 소리가 유일하게 심함 |
| 결선-2 | Chatterbox S3Gen만 Reference | 1위. 질문 억양은 어색하지만 그나마 나음 |
| 결선-3 | 현재 89분 Seed-VC | 2위. 질문 억양은 어색하지만 1번보다 나음 |

종합 순위는 `2 > 3 > 1`이다. 다만 세 경로 모두 Reference 같다고 느낄 수준은 아니어서
승자를 운영 기본값으로 채택하지 않는다. 이 결과로 다음을 확정한다.

- F0 보존율만으로 음질을 선택하지 않는다. 객관적 pitch 보존과 거친 음질은 별개다.
- 현재 Seed-VC에 데이터를 더 넣는 반복은 재개하지 않는다.
- Chatterbox의 S3Gen 조건은 이번 후보 중 가장 나은 비교 기준일 뿐 최종 해법이 아니다.
- 다음 실험은 같은 두 단계 조합의 미세 조정이 아니라, speaker identity와 prosody를
  함께 학습하거나 조건화하는 다른 TTS/voice adaptation 계열을 비교한다.

## Qwen3-TTS 0.6B 직접 합성 gate — 2026-08-14

두 단계 `generic TTS → voice conversion`의 구조적 손실을 피하기 위해
[`Qwen3-TTS-12Hz-0.6B-Base`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)의
공식 zero-shot voice cloning을 같은 여섯 문장으로 실행했다. 이 모델은 한국어와
voice cloning을 지원하고 Apache-2.0이며, 최종 복제 음성을 한 모델에서 직접 낸다.

RTX 2060의 FP16 생성은 첫 token sampling에서 NaN으로 실패했다. 같은 GPU/증상의
[공식 저장소 issue #43](https://github.com/QwenLM/Qwen3-TTS/issues/43)와 일치하며,
자동 dtype을 Turing에서는 FP32, Ampere 이상에서는 BF16으로 선택하도록 probe를
고쳤다. FP32에서는 6 GiB 카드에서 두 방식 모두 완주했다.

| 경로 | held-out 화자 유사도 | 질문/평서 끝 기울기 차이 | 평균 억양 폭 | local Whisper 내용 |
| --- | ---: | ---: | ---: | --- |
| 기존 S3Gen Reference 기준선 | **0.611** | 약 5.2 반음 | 약 1.84 반음 | 6/6 의미 보존 |
| Qwen ICL (음성+정확한 대본) | 0.585 | 1.40 반음 | **4.28 반음** | 1/6만 그대로 전사 |
| Qwen speaker embedding only | 0.533 | 3.21 반음 | 2.54 반음 | 5/6 의미 보존 |

여기서 Qwen은 Chatterbox 음성을 변환한 것이 아니므로 `보존율`이 아니라 Qwen 출력
자체의 질문/평서 차이를 적었다. ICL의 넓은 억양 폭도 `q2` 한 파일의 9.31 반음
outlier가 평균을 올린 결과라 자연스러움으로 해석하지 않는다.

ICL은 네 파일 앞부분이 local Whisper에서 요청하지 않은 `편안`으로 잡혔고, 두 질문은
문장이나 높임말까지 바뀌었다. 참조 대본과 참조 음성의 STT에는 `편안`이 없으므로
이번 현상을 참조 대사의 문자 그대로 누출이라고 단정할 수는 없지만, 반복되는 예상 밖
prefix라 실사용 gate에서는 실패다. 별도로 ICL의 참조 tail echo는
[공식 저장소 issue #341](https://github.com/QwenLM/Qwen3-TTS/issues/341)에도 보고돼 있다.

모델 cache가 있는 FP32 실행에서 load는 약 7초, 합성은 문장당 3.2~5.8초였다.
현재 운영 서비스는 바꾸지 않았다. ICL은 내용 충실성 때문에 운영 후보에서 제외하고,
embedding-only와 ICL의 음색 방향 자체가 fine-tuning을 검토할 가치가 있는지만
`review/qwen3-tts-listen/`에서 기존 S3Gen 기준선과 blind 청취한다. 세 경로 모두
불합격이면 Qwen zero-shot도 닫고, 같은 prompt/Seed-VC 미세 조정으로 돌아가지 않는다.

## 공개 Winter RVC speech 전이 gate — 2026-08-14

인터넷의 Winter AI cover가 현재 경로보다 나은지 확인하기 위해 공개된
[`WINTER of aespa [Strong Ver.] RVC v2`](https://voice-models.com/model/1lRYwEf9gyT)를
가져와 같은 Chatterbox 여섯 문장에 적용했다. 모델은 Hugging Face에서 공개·ungated로
배포된 450 epoch RVC v2 체크포인트와 retrieval index이며, 원본 zip의 SHA-256은
`6b8b0ff812c93f0805925d5b745679a8440ca8f7bf515b436582ab07d9760bec`이다.
실행기는 [Applio](https://github.com/IAHispano/Applio) commit
`085197e738ce9dd4c0bae1e0a74df5de25b89444`로 고정했다.

| 항목 | 공개 Winter RVC |
| --- | ---: |
| held-out 화자 유사도 | 0.236 |
| 질문/평서 끝 기울기 차이 보존 | -12% |
| 억양 폭 보존 | 73% |
| local Whisper 의미 보존 | 6/6 |
| warm 변환 시간 | 문장당 0.93~1.21초 |

held-out 화자 유사도는 S3Gen 기준선 0.611과 현재 Seed-VC 0.581보다 크게 낮았다.
질문 세 문장 중 두 문장만 STT 구두점에서 질문으로 잡혔고, 질문/평서 끝맺음의 방향도
보존하지 못했다. 반면 문장 내부 억양 폭과 처리 속도는 나쁘지 않았다.

이 수치만으로 공개 모델을 탈락시키지는 않는다. 이 모델의 주 사용례는 노래이며,
speech encoder 기반 유사도 평가도 singing-trained RVC에 불리할 수 있다. 공개 AI cover의
자연스러움은 원곡 가수가 제공한 timing, pitch, energy를 그대로 이용한 결과이므로,
일반 TTS가 만든 대화 음성에 적용했을 때 같은 품질을 보장하지 않는다. S3Gen 기준선 및
현재 Seed-VC와 함께 `review/rvc-public-listen/`에서 blind 청취한 뒤에만 판정한다.

이 체크포인트는 Human Reference의 임시 비교 자료일 뿐 운영 기본값이나 최종 겨울이
정체성으로 채택하지 않는다. 모델과 파생 음성은 로컬 ignored 경로에만 두며 외부
서비스로 보내지 않는다.
