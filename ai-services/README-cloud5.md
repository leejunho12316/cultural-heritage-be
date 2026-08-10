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

> **서브도메인 분리 권장**: FE와 BE를 같은 도메인 아래 서로 다른 서브도메인으로 나누는 걸 기본값으로 삼는다 — 예: `www.<도메인>`(또는 루트)은 FE(나중에 S3+CloudFront), `api.<도메인>`은 지금 이 문서가 다루는 백엔드 ALB. **도메인은 1개만 사면 되고, 서브도메인은 같은 호스팅 영역 안에서 레코드만 추가로 만들면 된다** — "FE용/BE용 도메인을 각각 사야 하나"로 헷갈리기 쉬운 지점이라 명시해둔다. FE를 다른 팀원이 담당한다면, 도메인을 누가 구매/소유하고 Route53 레코드를 누가 관리할지(권한 위임 또는 상대방이 레코드 요청 시 대신 추가) 미리 정해둘 것.

> **팀/공용 AWS 계정에서 구매 시 주의**: 도메인 등록 체크아웃 화면에 입력하는 등록자 정보(이름/이메일/주소/전화번호, WHOIS 정보)는 콘솔의 "프라이버시 보호(Privacy Protection)" 옵션을 켜도 **외부 공개 WHOIS 조회로부터만 숨겨질 뿐**, 같은 AWS 계정 안에서 조회 권한이 있는 root나 다른 IAM 사용자에게는 그대로 보인다(계정 리소스로 저장되기 때문). 개인 계정이 아니라 팀/공용 계정에서 진행 중이라면, 실명 대신 팀/프로젝트 대표 연락처를 쓰는 것도 방법이다(완전히 허위 정보는 소유권 검증 등에 문제가 될 수 있으니, 실존하는 연락처를 쓸 것).

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

---

## 부록 A — 실전 진행 기록 (도메인 `vora-heritage.click`, 2026-08-10)

이 절은 위 1~6단계를 실제 계정(`428270342381`)에서 처음부터 끝까지 밟아본 기록이다. 실제로 쓴 값, 검증에 쓴 CLI 명령과 결과, 그리고 진행 중 나온 질문과 답을 전부 남긴다(단, LLM 노드/프롬프트 고도화 관련 질문은 이 문서 범위가 아니라서 제외).

### 실전 적용값 요약
| 항목 | 값 |
|---|---|
| 도메인 | `vora-heritage.click` (`.click` TLD, 만료일 2027-08-10, 자동갱신 꺼짐) |
| 백엔드 서브도메인 | `api.vora-heritage.click` |
| FE용 서브도메인(예약, 아직 미착수) | `www.vora-heritage.click` |
| ACM 인증서 ARN | `arn:aws:acm:ap-northeast-2:428270342381:certificate/64434549-f54e-4b87-b230-c1d76d9fb0a1` |
| ALB | `k8s-default-conserva-a3c5743a4e-228691374.ap-northeast-2.elb.amazonaws.com` |

### HTTPS 작업 착수 전 — 개념 질문들

시작 전에 "EKS 엔드포인트를 http 대신 https로 만들고 싶다"는 목표에서 출발해, ACM/인증서/리스너 같은 용어를 몰라 여러 번 되물었다. 오간 질문과 정리된 답을 순서대로 남긴다.

- **질문 — "ACM, ALB Ingress 리스너, ACM 인증서, 자체 서명 인증서, 신뢰할 수 없는 인증서가 다 무슨 말인지 모르겠다"**: 봉투 밀봉(HTTPS 암호화)과 신분증(인증서) 비유로 설명했다. 처음엔 "봉투를 밀봉해서 보내는 사람 = 요청을 보내는 사용자(브라우저)"라고 잘못 짚는 질문이 나왔는데("보낸 사람이 찍은 도장인지 확인하는거고... 사용자가 제대로된 사용자인지랑 도메인이 맞는지랑 무슨 연관이 있어?"), **인증서를 제시하는 쪽은 사용자가 아니라 서버**라는 걸 은행 건물 비유로 정정했다: 방문자(브라우저)가 "국민은행" 간판이 걸린 건물(서버)을 찾아가기 전에, 건물이 먼저 시청(ACM 같은 인증 기관)이 발급한 정식 영업 허가증(인증서)을 보여줘서 "진짜 국민은행이 맞다"는 걸 증명한다는 구조. 사용자가 직접 정리한 요약("사용자가 브라우저를 통해 www.naver.com 접속... 사이트가 인증서로 증명하는거임")이 정확했다고 확인해줬다.
- **질문 — "ACM/도메인이 필요한 이유를 정리해봤는데 맞나"**: 사용자가 "AWS가 발급하는 인증서라서 믿을만하다"로 정리했던 것을, "ACM은 AWS 자체가 아니라 AWS가 제휴한 신뢰된 인증 기관(Amazon Trust Services)을 통해 무료로 발급을 대행해주는 창구"라고 한 군데만 정정했다. 도메인이 필요한 이유(ALB 자동 생성 주소는 AWS 소유라 소유권 증명 자체가 불가능하다는 부분)는 정확히 맞았다고 확인.
- **질문 — "지금 AWS CICD 방식을 채택한다면 아키텍처를 다르게 그려야 하는가"**: 이건 HTTPS가 아니라 15단계 CI/CD(GitHub+CodeStar Connections vs CodeCommit) 관련 질문이라 `README-cloud4.md` 쪽 내용이고, 이 문서와는 무관하다(다이어그램은 사용자가 직접 수정 완료).

### FE 연동 관련 질문

- **질문 — "FE가 지금 ALB 엔드포인트를 base_url로 쓰고 있는데, HTTPS로 바꾸면 FE는 어떻게 수정되어야 해?"**: 핵심은 `.env`의 API 베이스 URL 한 줄 교체뿐이라고 답했고, 부수적으로 세 가지를 짚었다 — ① 혼합 콘텐츠(Mixed Content): FE 자체가 이미 `https://`로 서빙 중이면 오히려 문제가 해소되는 방향이고, FE가 아직 `http://`(또는 `localhost`)라면 이번 변경과 무관하게 그대로임. ② 백엔드 CORS 설정(`WebConfig.java`의 `app.cors.allowed-origins`)은 "FE의 주소"를 등록하는 곳이라 백엔드 자신의 주소가 바뀌는 이번 건과 무관해서 안 건드려도 됨. ③ X-ray S3 presigned URL은 요청 시점에 백엔드가 동적으로 생성해 응답에 실어주므로 이번 변경과 무관.
- **질문 — "도메인이 www.conservation.com이면 FE 요청은 https://www.conservation.com/api/xray/health 이렇게 보내는거야?"**: 형식 자체는 맞다고 확인하되, `www`를 API용으로 쓰지 말고 `www`(또는 루트)는 FE, `api.`는 백엔드로 분리하는 걸 권장했다 — 나중에 FE CloudFront 배포를 재개했을 때 `www`를 이미 백엔드가 점유하고 있으면 충돌하기 때문. 이 권장이 그대로 채택되어 `api.vora-heritage.click`/`www.vora-heritage.click` 분리로 이어졌다.
- **질문 — "FE는 다른 개발자가 담당하는데, 그럼 www.conservation.com용 도메인이랑 api.conservation.com용 도메인 2개를 사야 하는거야?"**: 도메인은 1개만 사면 되고 `www`/`api`는 **같은 도메인의 서로 다른 서브도메인**(건물 비유: 도메인 = 건물 주소, 서브도메인 = 건물 안의 각 층 이름표)이라고 정정. 다만 도메인을 한 사람이 소유하게 되므로, FE 담당자와 "누가 사고 누가 레코드를 관리할지" 미리 정해야 한다는 점도 짚었다. → 이번엔 본인이 직접 구매하는 것으로 결정.

### 1단계 실전 — 도메인 구매

- **질문 — "체크아웃 페이지인데, 이 계정이 내 root 계정이 아니라 IAM 사용자로 온 거라 정보 입력이 꺼려진다. root가 이거 볼 수 있지?"**: 그렇다고 답했다 — 체크아웃에서 켜는 "프라이버시 보호"는 **외부 공개 WHOIS 조회로부터만** 등록자 정보를 숨겨주고, **같은 AWS 계정 내부의 root/다른 관리자에게는 그대로 보인다**(계정 리소스로 저장되므로). 이 계정이 커밋 로그(`공용계정용 배포 시도 3차`)로 미루어 팀/공용 계정으로 보인다는 점도 짚고, 대안으로 (a) 실명 대신 팀/프로젝트 대표 연락처 사용, (b) 별도 개인 계정에서 도메인만 구매하고 그 안의 레코드로 이 계정의 ALB를 가리키게 하는 크로스 계정 구성 두 가지를 제시했다. 사용자는 "내가 그냥 만들었어"로 그냥 본인 정보로 진행하기로 결정.
- **질문 — "도메인 등록까지 얼마나 걸리니?"**: TLD별로 다르며 `.com`/`.net`/`.org`류는 보통 몇 분~30분, `.io`/`.ai`나 국가 코드 TLD는 몇 시간~하루까지 걸릴 수 있다고 답했다. 실제로는 `vora-heritage.click`이 무리 없이 빠르게 등록 완료됨(`aws route53domains list-domains --region us-east-1`로 확인 — Route53 도메인 API는 리전이 없는 글로벌 서비스라 `us-east-1` 고정 호출이 필요하다는 점도 실전에서 확인됨).
- 이후 DNS 전파를 기다리며 세션을 일시 중단(`좋아 일단 이건 DNS 허용될때까지 보류할게`) — 다른 작업(BE CI/CD, 보존 가이드 노드 개선 등) 진행 후 나중에 재개.
- **재개 시 확인 방법**: `aws route53domains list-domains --region us-east-1 --query 'Domains[].{Domain:DomainName,Expiry:Expiry,AutoRenew:AutoRenew}'`로 등록 완료 여부와 만료일을 재확인했다.

### 2단계 실전 — ACM 인증서

- **분리 결정**: "api.vora-heritage.click으로 백엔드, www.vora-heritage.click는 FE로 분리하자"로 최종 확정 — 이번 인증서는 `api.vora-heritage.click` 하나만 발급(FE용 `www.` 인증서는 나중에 CloudFront 작업 재개 시 별도 발급 예정).
- Route53에 이미 등록된 도메인이라 "Route 53에서 레코드 생성" 버튼 한 번으로 DNS 검증용 CNAME이 자동 추가됐고, 발급까지 무리 없이 진행됨.
- **검증**: `aws acm list-certificates --region ap-northeast-2 --query 'CertificateSummaryList[].{Domain:DomainName,Status:Status,Arn:CertificateArn}'`로 `Status: ISSUED` 확인.

### 3단계 실전 — `k8s/app.yaml` 수정

실제 반영한 diff (Ingress `annotations`에 3줄 추가):
```diff
     alb.ingress.kubernetes.io/target-type: ip
-    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}]'
+    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}, {"HTTPS": 443}]'
+    alb.ingress.kubernetes.io/certificate-arn: "arn:aws:acm:ap-northeast-2:428270342381:certificate/64434549-f54e-4b87-b230-c1d76d9fb0a1"
+    alb.ingress.kubernetes.io/ssl-redirect: "443"
```
(각 줄의 의미는 3단계 본문의 "바뀐 부분 해설" 표와 동일 — `listen-ports`는 443 포트 리스너 추가, `certificate-arn`은 그 리스너가 제시할 인증서 지정, `ssl-redirect`는 80→443 자동 리다이렉트.)

`kubectl apply -f k8s/app.yaml` 후 `kubectl describe ingress conservation-backend`에서 `Events: SuccessfullyReconciled` 확인. 추가로 ALB 자체에 리스너가 실제로 붙었는지 CLI로 교차 검증:
```bash
aws elbv2 describe-listeners --load-balancer-arn <ALB ARN> --query 'Listeners[].{Port:Port,Protocol:Protocol}'
```
결과: `80/HTTP`, `443/HTTPS` 둘 다 확인됨.

### 4단계 실전 — Route53 레코드

- **질문 — "레코드가 뭐고, 선택한 각 항목의 의미하는게 뭔지 하나도 모르겠어"**: DNS를 전화번호부에, 레코드를 그 안의 한 항목("이 이름 → 실제 여기")에 비유해서 설명했다.
  - 레코드 이름 `api` → 도메인 앞에 붙어 `api.vora-heritage.click`이 됨.
  - 레코드 유형 `A` → "이름 → 주소" 매핑의 기본 형태.
  - 별칭(Alias) 켜기 → 고정 IP를 직접 적는 대신 ALB라는 AWS 리소스 자체를 가리키게 해서, ALB의 실제 IP가 바뀌어도 자동으로 따라가게 함(일반 A 레코드로 IP를 직접 박아넣으면 ALB가 재생성될 때마다 수동으로 고쳐야 함).
  - 트래픽 라우팅 대상 → 별칭이 정확히 어떤 리소스(ALB 종류 → 리전 → 실제 ALB 인스턴스)를 따라갈지 선택하는 부분.
- 레코드 생성 후 실제 반영 확인:
  ```bash
  aws route53 list-resource-record-sets --hosted-zone-id <Zone ID> --query "ResourceRecordSets[?Name=='api.vora-heritage.click.']"
  ```
  결과: `Type: A`, `AliasTarget.DNSName: dualstack.k8s-default-conserva-a3c5743a4e-228691374.ap-northeast-2.elb.amazonaws.com.` — 정상 연결 확인. (`dualstack` 접두사가 붙는 건 ALB가 IPv4/IPv6 이중 스택으로 응답 가능하다는 뜻으로, ALB 별칭 레코드에서 AWS가 자동으로 이렇게 생성한다.)

### 5단계 실전 — 검증

Windows PowerShell에서는 `curl`이 `Invoke-WebRequest`의 별칭이라, 실행 시 "스크립트 실행 위험" 확인 프롬프트가 뜬다(HTML 파싱 관련 보안 경고이며 API 호출 자체와는 무관 — `Y` 입력하면 진행됨). 결과:
```
StatusCode: 200
Content: {"aiServiceHealthy":true,"llmEnabled":true,"modelLoaded":true,"device":"cpu"}
```
SSL 관련 에러 없이 정상 응답 확인 — `https://api.vora-heritage.click`으로의 HTTPS 접속이 실제로 동작함을 확인했다.

### 6단계 — FE 반영 (기록 시점 기준 진행 예정)

FE 담당 개발자에게 API 베이스 URL을 `https://api.vora-heritage.click`으로 교체하도록 전달하는 것으로 이 문서의 실전 기록을 마친다. `www.vora-heritage.click`(FE용 도메인 연결)과 FE의 CloudFront 배포 자체는 이 세션에서 별도로 취소했던 작업이라 이 기록에는 포함하지 않는다.
