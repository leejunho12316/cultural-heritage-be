"""BE의 overallCondition 생성기가 부르는 Ollama 리버스 프록시.

BE(Java)는 이미 열려 있는 vca-ai용 ngrok 터널(8000번 포트) 하나만 갖고 있고,
무료 ngrok 계정은 고정 도메인을 하나만 준다 - Ollama(11434번, 같은 팟)를 위한
두 번째 고정 주소를 따로 받을 방법이 없다. 그래서 새 인프라를 추가하는 대신,
이미 뚫려 있는 vca-ai 자체가 `/ollama/api/chat`으로 받아서 localhost:11434로
그대로 전달(proxy)한다 - vca-ai 프로세스와 Ollama가 같은 팟에 있어서 내부
호출은 그냥 localhost다.
"""

from __future__ import annotations

import urllib.error
import urllib.request

OLLAMA_BASE_URL = "http://localhost:11434"
# BE(VcaOverallConditionGenerator)의 읽기 타임아웃(120s)보다 길어야 한다 -
# 짧으면 이 프록시가 BE보다 먼저 포기하고, Ollama가 그 뒤에 실제로 완성한
# 응답은 이미 끊긴 연결로 버려진다. 실측(2026-08-18): 콜드 로드(~15s) +
# findings가 많은 run의 생성 시간이 60s를 넘겨 여기서 먼저 잘렸다(Ollama
# 자체 GIN 로그에 "500 | 1m0s"로 남음 - 클라이언트가 연결을 끊어버린 뒤
# Ollama 쪽에서 뒤늦게 실패로 기록된 것으로 보인다).
_TIMEOUT_SECONDS = 150


class OllamaProxyError(RuntimeError):
    pass


def forward_chat(payload: bytes) -> bytes:
    """/api/chat 요청 바디를 그대로 로컬 Ollama에 전달하고 응답 바디를 돌려준다."""
    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise OllamaProxyError(f"Ollama request failed: {exc}") from exc
