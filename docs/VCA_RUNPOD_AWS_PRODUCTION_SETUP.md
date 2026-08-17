# AWS(EKS) 백엔드 ↔ RunPod vca-ai 연결 설정 절차

## 결론부터

**`k8s/app.yaml`을 git에서 고쳐서는 안 된다.** RunPod pod는 고정 하드웨어를 배정받는 게 아니라 새로 띄울 때마다 주소(pod-id)가 바뀌므로, `VCA_AI_BASE_URL`을 매니페스트에 리터럴로 박아두면 pod가 바뀔 때마다 커밋+apply를 반복해야 한다. 그래서 `k8s/app.yaml`은 이제 이 값을 `app-secrets`에서 `secretKeyRef`로 읽도록 바꿔뒀다 - **RunPod든 EKS 내부 모드든, 실제 전환은 전부 `app-secrets`의 값만 갱신하고 `rollout restart`하는 것으로 끝난다.** `ai-services/README-cloud4.md`에 기록된 실제 EKS/RDS/S3/ALB 배포에는 vca-ai가 아예 등장하지 않는다(서비스 표에 없음) - 이 문서가 다루는 건 그 구성에 vca-ai(RunPod 위탁)를 새로 편입시키는 절차다.

## 왜 RunPod 외부 위탁인가

이번 세션 전체가 이 경로를 향해 쌓아온 작업이다: `scripts/hybrid-runpod-tunnel.sh` + `scripts/pod-vca-ai-bare-start.sh`(로컬 개발 중 RunPod GPU를 SSH 터널로 붙여 검증), `docker-compose.remote-vca.override.yml`(원격 vca-ai/MinIO를 부르는 로컬 오버레이), `app/services/remote_io.py` + presigned URL 기반 이미지 핸드오프(EFS 없이 동작하게 만든 것) - 전부 "vca-ai는 GPU가 있는 RunPod에, 나머지는 AWS에"라는 목표 아키텍처를 로컬에서 미리 검증하기 위한 것이었다. `k8s/vca-ai-internal.yaml`(클러스터 내부에 vca-ai를 올리는 대안)도 이미 있지만, 그러려면 EKS에 GPU 워커 노드 그룹을 새로 만들어야 하고 RunPod를 쓰는 이점(온디맨드 GPU, 저렴한 단가)이 사라진다.

## 필요한 것 - 순서대로

### 1. RunPod: 안정적인 공개 URL 확보

지금까지 쓴 SSH 리버스 터널(`hybrid-runpod-tunnel.sh`)은 맥북이 켜져 있어야만 동작하는 로컬 개발용이다. 프로덕션에서 EKS가 붙으려면 팟이 **항상 켜져 있고 인터넷에서 직접 닿는 URL**을 내줘야 한다.

- RunPod 콘솔의 Pod 설정에서 vca-ai가 쓰는 포트(8000)를 HTTP 서비스로 노출 - **이미 열려 있음 확인됨**. `https://<pod-id>-8000.proxy.runpod.net` 형태의 프록시 URL이 나온다(`.env.runpod.example`에 이미 같은 패턴이 문서화돼 있음 - 9000/5173 포트용 예시 참고).
- 이 URL이 확정되면 4번에서 `app-secrets`의 `VCA_AI_BASE_URL` 값으로 넣는다. pod를 새로 띄워 이 값이 바뀌면 4번만 다시 하면 된다 - `k8s/app.yaml` 자체는 다시 안 건드림.

### 2. RunPod: vca-ai를 계속 떠 있게 하기

`scripts/pod-vca-ai-bare-start.sh`는 지금 포그라운드 프로세스다(터미널을 닫으면 죽음). 프로덕션에서는 팟이 재부팅되거나 프로세스가 죽어도 자동으로 다시 뜨는 방식이 필요하다 - `tmux`/`screen`으로 세션을 띄워두거나, RunPod 팟의 시작 커맨드 자체를 이 스크립트로 설정하거나, 최소한 `nohup ... &` + 워치독 스크립트 정도는 있어야 한다. (지금 범위 밖이지만, 이 단계 없이는 팟이 한 번 재시작되는 순간 서비스가 조용히 죽는다.)

### 3. 토큰 두 개 발급

이름이 비슷해서 헷갈리기 쉬운데 완전히 다른 두 홉(hop)을 지킨다:

| 환경변수 | 지키는 구간 | 코드 위치 |
|---|---|---|
| `VCA_ACCESS_TOKEN` | FE → BE | `application.yaml`의 `vca.access-token` (기본값 없음 - 없으면 Spring이 기동 자체를 거부). FE의 `VITE_VCA_ACCESS_TOKEN`과 정확히 같아야 한다. |
| `VCA_AI_ACCESS_TOKEN` | BE → vca-ai(RunPod) | `S3Config.internalS3Presigner`가 아니라 `RestClientConfig.vcaAiRestClient`가 `X-VCA-Access-Token` 헤더로 보낸다. vca-ai가 인터넷에 공개 노출되므로(내부 모드였으면 비워도 됐음) 이번엔 필수 - 단, 받는 쪽이 아직 이 헤더를 검증 안 함(아래 "확인된 보안 구멍" 참고). |

둘 다 랜덤 문자열(예: `openssl rand -hex 32`)로 새로 만든다.

### 4. `app-secrets`에 반영 (여기가 유일하게 실제로 값을 바꾸는 단계)

`README-cloud4.md` 11단계 기준 현재 `app-secrets`는 12개 키뿐이고 **VCA 관련 키가 하나도 없다**. `.env.k8s`(로컬에만 있는 파일, git에는 없음)에 아래 세 줄을 추가하고 시크릿을 재생성한다(템플릿은 `.env.example`의 VCA 섹션 참고):

```
VCA_ACCESS_TOKEN=<위에서 만든 값>
VCA_AI_ACCESS_TOKEN=<위에서 만든 값>
VCA_AI_BASE_URL=https://<pod-id>-8000.proxy.runpod.net   # 1번에서 확보한 URL
```

```bash
kubectl delete secret app-secrets
kubectl create secret generic app-secrets --from-env-file=.env.k8s
kubectl rollout restart deployment/conservation-backend
```

(`README-cloud4.md`가 이미 경고한 대로, `.env.k8s`만 고치고 시크릿 재생성 + rollout restart를 빼먹으면 반영 안 됨.) `k8s/vca-ai-internal.yaml`은 이 모드에서는 **apply하지 않는다** - 이미 떠 있다면 `kubectl delete -f k8s/vca-ai-internal.yaml`로 정리. `k8s/app.yaml` 자체는 이 시나리오에서 전혀 안 건드린다 - 이미 `apply -f k8s/app.yaml`로 떠 있다면 다시 apply할 필요도 없고, secret 갱신 + rollout restart만 하면 된다.

### 5. (참고) 나중에 EKS 내부 모드로 전환할 때

같은 방식으로, `app-secrets`의 `VCA_AI_BASE_URL`을 `http://vca-ai:8000`로 바꾸고 `kubectl apply -f k8s/app.yaml -f k8s/vca-ai-internal.yaml` + rollout restart만 하면 된다 - RunPod GPU 없이 EKS 안에서 vca-ai를 직접 돌리려면 GPU 워커 노드 그룹을 새로 만들어야 하니 그건 별도 작업.

### 6. S3는 그대로 둔다 (오히려 로컬보다 단순함)

로컬 하이브리드 테스트에서 썼던 `AWS_S3_INTERNAL_PRESIGN_ENDPOINT`(SSH 터널로 노출한 MinIO를 가리키던 값)는 **실제 AWS S3에서는 필요 없다** - S3 presigned URL은 원래 공개 인터넷 어디서나 열리는 정상 HTTPS URL이라, RunPod 팟이 아웃바운드로 그냥 직접 다운로드하면 된다. `app-secrets`의 기존 `AWS_S3_BUCKET`/`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`를 그대로 쓰면 되고, `AWS_S3_ENDPOINT`/`AWS_S3_PRESIGN_ENDPOINT`/`internal-presign-endpoint` 전부 비워둔 채로(현재 상태 그대로) 두면 `S3Config`가 알아서 진짜 AWS S3 엔드포인트로 동작한다.

## 확인된 보안 구멍 - vca-ai가 토큰을 아예 검증하지 않는다

`ai-services/vca-ai/app/` 전체를 확인한 결과, `X-VCA-Access-Token`(또는 `VCA_AI_ACCESS_TOKEN`) 관련 코드가 **하나도 없다**. `RestClientConfig`는 헤더를 보내지만 받는 쪽이 그 헤더를 읽지도, 검증하지도, 거부하지도 않는다 - 즉 3번에서 토큰을 만들고 4~5번에서 다 연결해도, vca-ai가 RunPod 프록시로 공개되는 순간 **누구나 인증 없이 `/internal/vca/*` 엔드포인트를 호출할 수 있다**(GPU 비용을 남이 쓰게 만들 수도 있음). 이 문서의 나머지 단계를 실행하기 전에, `ai-services/vca-ai/app/main.py` 또는 라우터 계층에 이 헤더를 검증하는 미들웨어/dependency를 먼저 추가해야 한다 - 별도 작업으로 처리 권장.

## 참고

- **CI/CD(`buildspec.yml`) 변경 불필요** - vca-ai는 이 모드에서 ECR/EKS를 안 거치므로(RunPod가 직접 서빙) 빌드 파이프라인에 추가할 게 없다. 앞서 발견한 계정 ID 불일치(`k8s/app.yaml`의 `428270342381` vs `k8s/vca-ai-internal.yaml`/`README-cloud4.md`의 `815373273907`)도 이 모드에서는 vca-ai 이미지를 아예 안 쓰므로 당장은 영향 없음 - 5번(내부 모드 전환)을 실제로 할 때 다시 확인.
