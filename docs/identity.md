# Companion Identity

Identity는 사용자 기억이나 모델 prompt 한 줄이 아니라, Companion의 안정적인 영속
데이터다. 아래 JSON을 Git 밖의 `data/identity.json` 등에 저장한 뒤 CLI에 경로를
명시한다. 이름·성격·관계 원칙은 천우가 결정하기 전까지 임의로 확정하지 않는다.

```json
{
  "name": "<chosen name>",
  "role": "local personal companion",
  "core_personality": ["<stable trait>"],
  "values": ["<stable value>"],
  "relationship_policy": ["<relationship rule>"],
  "immutable_boundaries": ["<boundary>"],
  "version": "1"
}
```

`core_personality`는 사용자 선호와 다르다. 대화 길이, 기술 설명 깊이처럼 대화에
따라 바뀔 수 있는 값은 이후 preference 기능에서 별도로 관리한다.
