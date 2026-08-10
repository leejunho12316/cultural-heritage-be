# HTTPS 적용 가이드 (도메인 + ACM + ALB HTTPS 리스너)

이 문서는 `README-cloud4.md`까지 끝낸 상태(EKS + ALB Ingress + CI/CD가 이미 동작 중)를 전제로, 지금 `http://<ALB 도메인>`으로만 열려 있는 API를 **도메인 + 진짜 HTTPS**로 접근 가능하게 만드는 절차다.

## 0단계 — 전체 흐름 개요

```
1. Route53에서 도메인 구매
2. ACM에서 그 도메인용 인증서 요청 → DNS로 소유권 검증 → 발급
3. ALB Ingress(k8s/app.yaml)에 443(HTTPS) 리스너 추가, 발급받은 인증서 연결
4. Route53에서 그 도메인이 ALB를 가리키도록 레코드 생성
5. https://<도메인>으로 접속 검증 + FE의 API 주소를 https로 교체
```

**핵심 개념 요약**(자세한 설명은 이 세션에서 이미 다룬 내용, 여기선 표로만 정리):
| 용어 | 한 줄 정의 |
|---|---|
| 도메인 | 우리가 실제로 소유권을 증명할 수 있는 주소(`myapp.com` 등). ALB의 자동 생성 주소(`....elb.amazonaws.com`)는 AWS 소유라 이 용도로 못 씀 |
| ACM 인증서 | "이 도메인은 진짜 신청자 소유가 맞다"를 검증한 뒤 발급하는 디지털 신분증. 서버(ALB)가 이걸 들고 있으면 브라우저가 "진짜 그 도메인이 맞구나"라고 신뢰함 |
| ALB HTTPS 리스너 | ALB가 443번 포트로 들어오는 요청도 받도록(그리고 그 요청에 ACM 인증서로 응답하도록) 여는 설정 |

---

## 1단계 — Route53 도메인 구매

Route53 콘솔 → 등록된 도메인 → 도메인 등록

1. 원하는 도메인 이름 검색(예: `conservation-heritage.com`) → 사용 가능 여부 확인
2. 장바구니 담기 → 연 단위 결제 진행(TLD에 따라 다름, `.com` 기준 대략 연 $12~15)
3. 등록 정보(연락처 등) 입력 → 결제 완료

> **소요 시간**: 대부분 몇 분 내로 등록되지만, TLD에 따라 최대 몇 시간 걸릴 수 있다. 등록이 끝나면 Route53이 이 도메인용 **퍼블릭 호스팅 영역(Hosted Zone)**을 자동으로 만들어준다 — 3단계에서 이걸 그대로 쓴다.

> **비용 참고**: 도메인 자체 연 사용료 외에, 호스팅 영역 자체도 **월 $0.50** 별도 청구된다(도메인을 다른 곳에서 사고 DNS만 Route53으로 옮겨도 마찬가지). 이 금액은 도메인을 갖고 있는 한 계속 나가는 고정비다.

---

## 2단계 — ACM 인증서 요청 + 발급

**리전 주의**: 인증서는 그 인증서를 실제로 붙일 리소스(ALB)와 **같은 리전**에서 요청해야 한다. ALB가 `ap-northeast-2`(서울)에 있으므로 ACM도 콘솔 우측 상단 리전을 `ap-northeast-2`로 맞추고 진행한다. (CloudFront에 붙이는 인증서라면 예외적으로 `us-east-1` 고정이지만, 이번엔 ALB라 해당 없음.)

ACM 콘솔 → 인증서 요청
| 항목 | 값 |
|---|---|
| 인증서 유형 | 퍼블릭 인증서 요청 |
| 정규화된 도메인 이름 | `conservation-heritage.com` (루트 도메인). API를 서브도메인(`api.conservation-heritage.com`)으로 쓸 계획이면 그 이름을, 여러 서브도메인에 두루 쓰고 싶으면 `*.conservation-heritage.com`(와일드카드)도 같이 추가 가능 |
| 검증 방법 | **DNS 검증**(권장 — 이메일 검증보다 자동화하기 쉽고, 인증서 자동 갱신도 DNS 검증이라야 됨) |
| 키 알고리즘 | 기본값(RSA 2048) 그대로 |

요청 후 인증서 상세 페이지에서:
1. "Route 53에서 레코드 생성" 버튼 클릭 (1단계에서 도메인을 Route53으로 등록했다면 이 버튼 하나로 검증용 CNAME 레코드가 자동으로 추가된다 — 수동으로 값을 복사/붙여넣기 안 해도 됨)
2. 상태가 `검증 대기 중` → `발급됨(Issued)`으로 바뀔 때까지 대기(보통 몇 분, 늦으면 30분 정도)

발급 완료된 인증서의 **ARN**(`arn:aws:acm:ap-northeast-2:<계정ID>:certificate/xxxxxxxx-...`)을 복사해둔다 — 다음 단계에서 그대로 쓴다.

---

## 3단계 — ALB Ingress에 HTTPS 리스너 추가

`k8s/app.yaml`의 `Ingress` 리소스 `annotations`에 3개를 추가/수정한다:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: conservation-backend
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}, {"HTTPS": 443}]'
    alb.ingress.kubernetes.io/certificate-arn: "arn:aws:acm:ap-northeast-2:<계정ID>:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    alb.ingress.kubernetes.io/ssl-redirect: "443"
spec:
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: conservation-backend
                port:
                  number: 8080
```

**바뀐 부분 해설**:
| annotation | 의미 |
|---|---|
| `listen-ports`에 `{"HTTPS": 443}` 추가 | ALB가 443번 포트도 받도록 리스너를 하나 더 연다 |
| `certificate-arn` | 443 리스너가 방문자에게 보여줄 신분증(2단계에서 발급받은 ACM 인증서)을 지정 |
| `ssl-redirect: "443"` | 80번(HTTP)으로 들어온 요청을 자동으로 443번(HTTPS)으로 튕겨 보낸다 — 이 설정이 있으면 `http://`로 접속해도 브라우저가 자동으로 `https://`로 바뀐다. `path`가 `/`인 룰 하나만 있어도 컨트롤러가 알아서 HTTP→HTTPS 리다이렉트 룰을 추가로 만들어준다 |

적용:
```bash
kubectl apply -f k8s/app.yaml
```
AWS Load Balancer Controller가 이 변경을 감지해서 기존 ALB에 443 리스너를 추가하고, 보안 그룹에 443 인바운드 규칙도 자동으로 추가한다(직접 보안 그룹을 편집할 필요 없음). 반영까지 1~2분 정도 걸린다.

---

## 4단계 — Route53에서 도메인을 ALB로 연결

Route53 콘솔 → 호스팅 영역 → (1단계에서 자동 생성된) 도메인 선택 → 레코드 생성

| 항목 | 값 |
|---|---|
| 레코드 이름 | API용 서브도메인을 쓸 경우 `api` 입력(예: `api.conservation-heritage.com`이 됨), 루트 도메인 그대로 쓸 거면 비워둠 |
| 레코드 유형 | `A` |
| 별칭(Alias) | **켜기** — 별칭을 켜면 고정 IP 대신 ALB를 직접 가리킬 수 있고, ALB의 IP가 바뀌어도(ALB는 IP가 고정이 아님) 자동으로 따라감. 별칭을 안 쓰고 일반 A 레코드로 ALB의 현재 IP를 직접 박아넣으면 나중에 ALB가 재생성됐을 때(19단계 클러스터 재구축 등) 레코드를 매번 수동으로 고쳐야 해서 비권장 |
| 트래픽 라우팅 대상 | "Application Load Balancer 및 Classic Load Balancer에 대한 별칭" → 리전 `ap-northeast-2` → 3단계에서 443 리스너를 추가한 그 ALB 선택 |

레코드 생성 후 DNS 전파까지 보통 몇 분(최대 몇십 분) 걸린다.

---

## 5단계 — 검증

```bash
curl -v https://<도메인>/api/xray/health
```
- `curl` 응답이 정상 오고, `-v` 출력에 인증서 관련 에러(`SSL certificate problem` 등)가 없으면 성공.
- 브라우저로 `https://<도메인>`에 접속해서 주소창에 자물쇠 아이콘이 뜨는지 확인.
- `http://<도메인>`으로 접속했을 때 자동으로 `https://<도메인>`으로 리다이렉트되는지 확인(3단계 `ssl-redirect` 동작 확인).

---

## 6단계 — FE 쪽 반영

FE가 지금까지 써온 API 베이스 URL(`http://k8s-default-....elb.amazonaws.com`)을 새 도메인으로 교체한다:
```
https://<도메인>
```
(서브도메인을 썼다면 `https://api.<도메인>`) FE 코드의 환경변수(`.env` 등)에서 이 값을 바꾸고 재배포하면 된다.

> **주의 — 혼합 콘텐츠(Mixed Content)**: FE 자체가 `https://`로 서빙되고 있는데 내부적으로 API를 `http://`로 호출하면 브라우저가 그 요청을 차단한다. FE와 백엔드 둘 다 HTTPS로 맞춰야 정상 동작한다(FE를 아직 CloudFront/HTTPS로 안 올렸다면, FE 쪽도 같이 HTTPS로 서빙해야 이 백엔드 HTTPS를 문제없이 호출할 수 있다).

---

## 트러블슈팅

| 증상 | 원인 후보 | 확인 |
|---|---|---|
| ACM 인증서 상태가 오래 `검증 대기 중`에 머묾 | DNS 검증용 CNAME 레코드가 아직 전파 안 됨, 또는 "Route 53에서 레코드 생성" 버튼을 안 눌러서 레코드 자체가 없음 | Route53 호스팅 영역에서 ACM이 요구하는 이름의 CNAME 레코드가 실제로 있는지 확인 |
| `kubectl apply` 후에도 ALB에 443 리스너가 안 생김 | annotation 오타(`listen-ports` JSON 형식 오류 등), 또는 `certificate-arn`이 잘못됨/다른 리전 인증서 | `kubectl describe ingress conservation-backend`의 Events 확인, ACM 인증서가 ALB와 같은 리전(`ap-northeast-2`)에 있는지 재확인 |
| `curl`에서 `SSL certificate problem: unable to get local issuer certificate` | 아직 DNS 전파가 덜 끝났거나 잘못된 도메인으로 접속 중 | `dig <도메인>` 또는 `nslookup <도메인>`으로 ALB 주소를 제대로 가리키는지 확인, 몇 분 후 재시도 |
| 브라우저에서 "안전하지 않음" 경고 | `certificate-arn`을 빠뜨려서 여전히 자체 서명/기본 인증서로 응답 중이거나, ACM 인증서 자체가 아직 `발급됨` 상태가 아님 | ACM 콘솔에서 인증서 상태 재확인 |
| FE에서 API 호출이 브라우저 콘솔에 조용히 실패(Mixed Content) | FE는 HTTPS인데 API 주소만 HTTP로 남아있음 | FE의 API 베이스 URL을 `https://`로 교체(6단계) |

---

## 비용 참고

| 항목 | 비용 |
|---|---|
| 도메인 등록 | TLD별 상이, `.com` 기준 연 $12~15 |
| Route53 호스팅 영역 | 월 $0.50(도메인을 갖고 있는 한 계속 청구) |
| ACM 인증서 | **무료**(ALB/CloudFront 등 AWS 리소스에 붙이는 용도로 쓰면 발급/갱신 전부 무료) |
| ALB 443 리스너 추가 | 추가 요금 없음(기존 ALB의 시간당 요금에 포함, 리스너 개수로 별도 과금 안 함) |

즉 이 작업으로 늘어나는 고정비는 **도메인 연 사용료 + 월 $0.50(호스팅 영역)** 뿐이다.
