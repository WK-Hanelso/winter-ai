# Milestone 2 검증 기록

검증 일자: 2026-08-06

## 결론

Milestone 2의 CLI/Core 기반 수직 단면을 통과했다. 겨울이의 Identity는 Git에 포함되지
않는 local JSON에서 유지되고, 사용자가 명시적으로 요청한 기억만 후보가 된다. 후보는
검토와 활성화 전까지 Local LLM context에 전달되지 않는다.

```text
대화: “기억해. 나는 Python config를 선호해”
  → candidate 생성 및 저장 안내
  → CLI approve
  → CLI activate
  → 관련 질문에서 active Memory만 context로 검색
  → replace 시 새 candidate 생성
  → 새 항목 activate 시 이전 항목 deprecated
  → 명시적 delete 또는 교체 이력 보호
```

## 재현한 검증

Docker의 fake backend와 테스트 전용 SQLite DB에서 아래 결과를 확인했다.

| 항목 | 결과 |
| --- | --- |
| `data/identity.json` 재조회 | 이름 `겨울이`, version `1` 및 Core Persona 유지 |
| 대화 기반 후보 생성 | `기억해. 나는 Python config를 선호해`가 semantic `candidate` 하나 생성 |
| 후보 안내 | CLI 출력과 Voice TTS 요청 텍스트에 “검토 후 활성화” 안내 포함 |
| lifecycle | `candidate → approved → active` 성공 |
| 교체 | 새 candidate 활성화 시 원본이 `deprecated`, 새 항목이 `active` |
| 이력 보호 | 새 항목이 `supersedes`로 참조한 원본의 delete 요청을 거부 |
| 물리 삭제 | 참조되지 않는 항목은 list·retrieval·SQLite에서 제거 |
| 기본 회귀 | `docker compose run --rm dev pytest` — 45 passed |

active Memory retrieval과 Local LLM request context 주입은 unit test에서 별도로
검증한다. 실제 Qwen3 local CLI와 active Memory 주입의 검증은 #41에서 수행했다.

## 실행 명령

```bash
export LOCAL_UID=$(id -u)
export LOCAL_GID=$(id -g)

docker compose run --rm dev pytest

docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --memory-db /workspace/data/memories.sqlite \
  --prompt "기억해. 나는 Python config를 선호해"

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --list-memories
```

검증 뒤 개인 기억을 지우려면 `--memory-delete <id>`를 명시적으로 실행한다. 이 명령은
복구되지 않는다. 다른 Memory가 해당 ID를 `supersedes`로 참조할 때는 삭제가 거부된다.

## M2 이후의 한계

- 기억 후보의 승인·활성화는 아직 CLI ID 명령으로만 한다. Voice 대화만으로 확정하지
  않는다.
- 명시적 `기억해` 또는 `기억해줘` 형식만 결정론적으로 인식한다.
- 검색은 keyword overlap 기반이다. embedding/vector search, 자동 conflict 판단,
  장기 선호의 반복 관찰 기반 갱신은 다음 단계다.
- 실제 마이크·스피커 기반 Voice round trip은 #34의 수동 하드웨어 검증이 남아 있다.
- 데이터 파일(`data/`, SQLite, 음성, 모델)은 Git에 저장하지 않는다.
