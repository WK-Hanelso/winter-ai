# Winter Web Ingress Safety

## 사용자 결과

폰은 기존 Tailscale HTTPS 주소를 사용한다. Winter Web 자체는 Host의 `127.0.0.1:8000`에만
대기하므로 같은 LAN의 다른 장치가 8000 포트로 직접 접근할 수 없다. Tailscale Serve가
인증된 tailnet 연결을 loopback Web으로 전달한다.

```text
phone browser
  → Tailscale HTTPS
  → Tailscale Serve
  → 127.0.0.1:8000
  → Winter Web
```

## 구현 계약

- `companion.web`의 기본 host는 `127.0.0.1`이다.
- 운영 systemd unit도 `--host 127.0.0.1`을 명시한다.
- POST body는 기본 16 MiB까지 허용한다.
- `Content-Length`가 없으면 411, 숫자가 아니거나 음수면 400, 상한을 넘으면 413을 반환한다.
- body 제한을 통과하기 전에는 STT, Core, Worker와 SQLite handler를 호출하지 않는다.
- 다른 interface가 필요한 operator만 `--host`를 명시적으로 override할 수 있다.

16 MiB는 현재 push-to-talk WebM turn에 충분한 여유를 주면서 무제한 메모리 읽기를 막는
초기 상한이다. 이것은 rate limiting, account authentication 또는 slow-client timeout을
대체하지 않는다.

## 검증

```bash
docker compose run --rm dev \
  pytest -q tests/unit/test_web_ingress.py \
  tests/unit/test_web_voice_metrics.py \
  tests/unit/test_web_voice_path.py
```

테스트는 parser와 deploy unit의 loopback, 400/411/413 응답, handler 미호출과 정상 health/
phone path를 고정한다.

## 운영 반영

운영 서비스 변경은 진행 중인 대화를 끊을 수 있으므로 자동 재시작하지 않는다. 반영할 때는
다음 순서로 실행한다.

```bash
install -m 0644 deploy/systemd/winter-web.service \
  "$HOME/.config/systemd/user/winter-web.service"
systemctl --user daemon-reload
systemctl --user restart winter-web.service
systemctl --user status winter-web.service --no-pager
curl -fsS http://127.0.0.1:8000/health
tailscale serve status
```

그 뒤 `ss -ltn 'sport = :8000'`에서 `127.0.0.1:8000`만 보여야 한다. 폰의 일반적인
push-to-talk 대화는 이후 실사용 관찰에 포함하지만, 변경하지 않은 음성 기능을 다시 수동
시연하는 것을 이 ingress 보안 수정의 activation gate로 삼지는 않는다.

## 2026-08-19 운영 검증

- 설치된 user unit과 repository unit이 일치하고 서비스가 새 설정으로 재시작됐다.
- 실제 socket은 `127.0.0.1:8000`에만 열렸고 `0.0.0.0:8000` listener는 없다.
- loopback `/health`가 `ok`를 반환했다.
- 실행 중인 서버에 16 MiB를 넘는 `/turn`을 보내 HTTP 413과 명시적 오류 본문을 확인했다.
- Tailscale Serve는 tailnet 전용 HTTPS hostname을 `127.0.0.1:8000`으로 전달한다.
- 서버의 Tailscale IP로 hostname을 resolve해 HTTPS `/health=ok`를 확인했으므로 인증서와
  Serve proxy를 포함한 ingress 경로가 실제로 동작한다.

이 증거는 #114의 범위인 직접 LAN 노출 제거와 request body 상한을 실제 운영 process에서
확인한다. Android peer가 당시 offline이어서 폰 시연은 하지 않았지만, 이는 보안 경계의
성공 여부를 바꾸지 않는다.

## 복구

loopback Web의 local health가 실패하면 먼저 service log와 Tailscale Serve target을
확인한다. 이전 unit은 preservation commit `cb4bb4b`에서 확인할 수 있다. 이전 unit으로
되돌리면 `0.0.0.0:8000` LAN 노출도 함께 돌아오므로, 단순 접속 문제의 기본 해결책으로
사용하지 않는다.
