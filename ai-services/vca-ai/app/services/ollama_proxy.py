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
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise OllamaProxyError(f"Ollama request failed: {exc}") from exc
