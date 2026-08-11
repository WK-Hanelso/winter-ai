# 음성 출력 경로

겨울이가 대답을 소리로 내는 첫 경로입니다. 타자로 묻고 음성으로 듣습니다.
마이크 입력은 아직 없습니다. 실행은 `./winter voice`입니다.

이 문서는 무엇이 어디서 도는지, 왜 그렇게 나뉘었는지, 그리고 지금 무엇이
느리고 무엇이 아직 겨울이의 목소리가 아닌지를 기록합니다.

## 무엇이 어디서 도는가

| 조각 | 실행 위치 | 이유 |
| --- | --- | --- |
| `companion.voice_cli` | **Host** | 합성이 `docker run`을, 재생이 Host 사운드 장치를 필요로 함 |
| llama.cpp 모델 서버 | `llm` container | 기존과 동일 |
| MeloTTS 합성 | `winter-ai:melotts-probe` container | 9.81 GiB image를 상주시키지 않기 위해 요청마다 실행 |

`./winter start`와 `./winter chat`은 dev container 안에서 돌기 때문에 Compose
내부 주소 `http://llm:8080`을 씁니다. `voice`만 Host에서 돌기 때문에 그 주소에
닿을 수 없고, 모델 서버를 Host에도 공개해야 합니다. 그 공개는
`compose.llm-host.yaml` overlay로 분리되어 `voice`에서만 적용됩니다.

포트는 `0.0.0.0`이 아니라 `127.0.0.1`에 묶습니다. local-first는 모델이 이
컴퓨터에만 대답한다는 뜻이고, 같은 네트워크의 다른 기기에 열어 줄 이유가
없습니다.

준비 확인(health check)도 dev container를 거치지 않고 Host에서 직접
`127.0.0.1:8080`을 두드립니다. 실제로 쓰게 될 주소가 아닌 다른 주소를 확인하면,
확인은 통과하는데 정작 경로는 막혀 있는 상태가 나올 수 있습니다.

## 시행착오

### `--user`로 실행하면 합성이 죽는다

출력 wav가 root 소유로 남는 것을 막으려고 container를 Host 사용자로 실행하면
합성이 이렇게 실패했습니다.

```
RuntimeError: cannot cache function '__shear_dense':
no locator available for file '/usr/local/lib/python3.9/site-packages/librosa/util/utils.py'
```

image가 caching 경로를 전부 `/root` 아래에 두고 만들어졌기 때문입니다. `/root`는
root 전용(`drwx------`)이라 다른 사용자는 들어가지도 못하고, 갈 곳을 잃은 numba가
librosa 소스 옆에 caching을 시도하다 실패합니다. 오류 메시지가 numba와 librosa를
가리켜서 합성 자체의 문제처럼 읽히지만, 원인은 권한입니다.

image를 다시 굽지 않고, 쓸 수 있는 mount 한 곳으로 caching 경로를 전부
돌려서 해결했습니다.

```
-v <cache_dir>:/cache:rw
-e HOME=/cache -e HF_HOME=/cache/huggingface
-e NUMBA_CACHE_DIR=/cache/numba -e MPLCONFIGDIR=/cache/matplotlib
```

`HF_HOME`을 명시하는 것이 `HOME`에 기대는 것보다 낫습니다. 이미 받아 둔 모델이
그 경로에 있고, 기본값이 바뀌어도 흔들리지 않습니다.

이 네 변수는 test로 고정되어 있습니다. 특히 `NUMBA_CACHE_DIR`이 빠지면 위 실패가
그대로 돌아옵니다.

## 측정값

한 turn을 끝까지 돌린 결과입니다. 문장 하나 기준입니다.

| 항목 | 값 |
| --- | --- |
| 한 turn 전체 | 28.7초 |
| 그중 합성 | 17.6초 |
| 출력 형식 | 44.1 kHz mono 16-bit PCM wav |
| 모델 cache | 658 MiB (첫 실행에서 내려받고 이후 재사용) |

합성 17.6초의 대부분은 model 적재입니다. 요청마다 container를 새로 띄우는 대신
9.81 GiB를 상주시키면 사라지는 비용이며, 지금은 **동작하는 것**이 목표라 그
교환을 받아들였습니다. 실시간 대화를 하려면 이 부분을 상주 process로 바꿔야
합니다.

## 지금 하지 않는 것

- **이 목소리는 Reference의 목소리가 아닙니다.** MeloTTS는 한국어 화자를 하나만
  제공하고 목소리 복제를 하지 못합니다. 목소리를 겨울이의 것으로 바꾸는 일은
  Order 7이고, adapter를 한 class로 분리해 둔 이유도 그 교체를 한 곳에서 끝내기
  위해서입니다.
- **말투 profile 기본값은 `base`입니다.** 측정으로 얻은 Reference profile은 한
  문장 8단어로 대답을 자르는데, 이는 Reference가 말하는 방식의 충실한 재현인
  동시에 질문에 답하는 방식으로는 나쁩니다. 발표를 망쳤다는 말에 "그래, 좀
  힘들어 보여"라고만 답하고 질문은 그대로 두었습니다. `--style`로 바꿀 수
  있으며, 기본값을 뒤집는 것은 세부사항이 아니라 결정입니다.
- **마이크 입력이 없습니다.** 말해서 묻는 경로는 별도이고, 먼저 이쪽이 돌아야
  합니다.
- **대화가 저장되지 않습니다.** `voice`는 메모리 안에서만 대화를 유지하며,
  종료하면 사라집니다. `start`/`chat`이 쓰는 `data/`의 SQLite와 아직 연결되어
  있지 않습니다.

생성된 wav는 `generated_audio/voice/`에 남고, 이 경로와 `*.wav`는 Git에서
제외됩니다.
