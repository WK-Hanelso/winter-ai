# Jetson AGX Orin (JetPack 5, L4T r35.4.1)

겨울이 스테이지 1을 Orin에서 돌리기 위해 만든 이미지들. 빌드 순서는
`Dockerfile.torchaudio` 다음 `Dockerfile.chatterbox` 이고, 두 번째가 첫 번째를
베이스로 쓴다.

```bash
docker build -f Dockerfile.torchaudio -t winter-ai:jetson-torchaudio .
docker build -f Dockerfile.chatterbox -t winter-ai:jetson-chatterbox .
```

## 이 보드에서 걸렸던 것들

네 가지가 순서대로 걸렸고, 각각의 이유는 해당 파일 주석에 적어두었다.

1. **torchaudio가 없다.** JetPack 5 이미지 중 torch 2.1과 torchaudio를 같이
   주는 것이 없다. PyPI 것은 다른 libtorch에 링크되어 `undefined symbol`로
   죽는다. 그래서 보드의 torch에 맞춰 소스에서 빌드한다 (약 25분).
2. **`parametrizations.weight_norm`이 없다.** 베이스의 torch는
   `2.1.0a0+nv23.06`, 즉 그 함수가 들어오기 **전에** 잘라간 알파다. 버전
   번호만 2.1이다. `winter_weight_norm.py`가 상류 구현을 그대로 옮겨 채운다.
   구 API(`torch.nn.utils.weight_norm`)로 갈아끼우지 않은 이유는 그 파일에.
3. **SDPA를 쓸 수 없다.** transformers는 torch 2.1.1 미만에서 SDPA를
   거부하는데, 2.1.0의 SDPA가 비연속 입력에서 수치를 틀리기 때문이다
   (pytorch#112577). 버전 문자열 문제가 아니라 실제 버그이므로, 우회하지 않고
   eager로 돌린다.
4. **cffi 누락이 네 단계 뒤에 나타난다.** perth가 자기 워터마커의 ImportError를
   삼키고 이름을 None으로 두어서, 모델을 만드는 시점에 `\'NoneType\' object is
   not callable`로 터진다. 그래서 이미지 검증이 `import chatterbox`에서 멈추지
   않고 워터마커가 살아 있는지까지 확인한다.

## 측정된 속도 (2026-08-13)

같은 세 문장, 오디오 1초당 연산 시간(RTF):

| | RTF |
| --- | --- |
| RTX 2060 (sdpa) | 1.00 |
| AGX Orin (eager, MAXN, jetson_clocks) | 2.1 ~ 2.8 |

`nvpmodel`은 이미 MAXN이었고 `jetson_clocks`로 1.3 GHz에 고정해도 달라지지
않았다. 하드웨어 자체가 이 작업에서 느리다:

| | 2060 | Orin |
| --- | --- | --- |
| fp32 matmul | 4.57 TFLOPS | 2.85 TFLOPS |
| 메모리 대역폭 | 297 GB/s | 169 GB/s |

자기회귀 디코딩은 대역폭에 묶이므로 1.76배 차이가 그대로 나타나고, 나머지는
eager attention 몫으로 보인다. Orin이 이기는 것은 속도가 아니라 메모리다.
30 GB 통합 메모리라 LLM과 음성이 한 장에 같이 올라간다.
