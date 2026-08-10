# AWS ECR + EKS 배포 가이드 (3차 — 최종 아키텍처, `BigProject09_AWS.drawio.png` 기준)

이 문서는 `ai-services/BigProject09_AWS.drawio.png`에 확정된 아키텍처를 그대로 구현하는 절차다. 2차 배포(`README-cloud2.md`)와 비교하면 다음 5가지가 새로 생기거나 크게 바뀐다.

| 구분 | 2차 배포 | 3차 배포(이 문서) |
|---|---|---|
| VPC | 기본(default) VPC 그대로 사용 (전부 퍼블릭 서브넷) | **커스텀 VPC** — AZ 2개 × (퍼블릭/프라이빗-컴퓨팅/프라이빗-데이터) 서브넷 6개, NAT Gateway 2개 |
| RDS | 단일 AZ (`db.t3.micro` 프리티어) | **Multi-AZ**(대기 인스턴스 자동 복제) — 프리티어 범위 밖, 비용 증가 |
| 외부 노출 | `conservation-backend` Service를 `type: LoadBalancer`로 변경(NLB류 자동 생성) | **ALB Ingress** — AWS Load Balancer Controller를 설치해 진짜 ALB를 씀 (2차의 "남은 작업" 1번을 여기서 해결) |
| 배포 자동화 | 로컬에서 수동 `docker build/push` + 수동 `kubectl apply` | **CI/CD** — CodeCommit → CodePipeline → CodeBuild → ECR (2차의 "남은 작업" 3번을 여기서 해결) |
| X-ray 결합 파이프라인 | `conservation-backend`/`xray-ai`가 EFS(`/shared`) 공유 볼륨으로 파일을 주고받음 | **S3 + RDS가 정본** — EFS를 완전히 걷어내고, FE가 S3에 직접 업로드/다운로드하며 Lambda가 업로드 이벤트를 RDS에 기록 (이미 main에 병합된 코드, PR #22) |

> **이 버전(v2)에서 바뀐 점**: 처음 이 문서를 쓸 때는 X-ray 결합에 EFS(공유 볼륨)가 필요하다고 가정했다. 그 사이 `feature/xray-s3-rds-final-test`(PR #22)가 main에 병합되면서 X-ray 파이프라인이 **S3 + RDS 기반으로 전면 재작성**됐고, EFS는 더 이상 필요 없다. 이에 맞춰 EFS 관련 단계를 전부 제거하고, 실제 코드(`docs/XRAY_S3_RDS_INTEGRATION.md`, `aws/lambda/xray-s3-file-recorder/`)를 기준으로 S3 + Lambda 단계를 다시 썼다.

이 문서는 2차와 마찬가지로 AWS 계정에 **아무것도 없는 상태**(또는 2차 자원과 별도로 새 VPC에 새로 구축하는 상태)를 가정한다. 계정ID `815373273907` / 리전 `ap-northeast-2`(서울) 기준으로 쓰고, AZ는 `ap-northeast-2a`(이하 AZ-A) / `ap-northeast-2c`(이하 AZ-C) 2개를 쓴다. 새로 만들 때마다 값이 달라지는 것(서브넷 ID, RDS 엔드포인트 등)은 `<...>`로 표기했다.

> **먼저 결정할 것**: 2차에서 만든 EKS 클러스터/RDS가 기본 VPC 위에 그대로 남아있다면, 이번 아키텍처는 **커스텀 VPC가 필수**라서 기존 클러스터를 그 안으로 옮길 수 없다(VPC는 나중에 못 바꾸는 리소스). 즉 이번 작업은 "업그레이드"가 아니라 **새 VPC에 새로 짓는 것**이다. 2차 자원을 계속 쓸 계획이 없다면, 비용이 겹치지 않도록 미리 정리(EKS 노드 그룹 축소/삭제, RDS 삭제, 2차에서 만든 EFS 삭제 등)해두는 걸 권장한다.

---

## 0단계 — 아키텍처 개요

### 구성 요소
| 구성 요소 | 역할 | 배치 위치 |
|---|---|---|
| Route 53 | DNS | AWS 글로벌 서비스 (VPC 밖) |
| IGW | 인터넷 게이트웨이 | VPC 경계 |
| ALB | 유일한 외부 진입점, `conservation-backend`로 라우팅 | 퍼블릭 서브넷 (AWS Load Balancer Controller가 자동 배치) |
| NAT Gateway ×2 | 프라이빗 서브넷의 아웃바운드(예: OpenAI API 호출) | 각 AZ 퍼블릭 서브넷 |
| EKS 워커 노드(EC2) | `conservation-backend`/`conservation-guide-ai`/`xray-ai`/`pottery-inspection-ai` Pod 실행 | 각 AZ 프라이빗 컴퓨팅 서브넷 |
| RDS(PostgreSQL, Multi-AZ) | 앱 DB + LangGraph 체크포인터 + X-ray 작업/파일 메타데이터(`xray_job`/`xray_defect`/`s3_file`) | 프라이빗 데이터 서브넷 (Primary/Standby 각 AZ) |
| S3 | X-ray 입력(컬러/조각 이미지) + 결합·최종화 산출물의 **정본 저장소** | AWS 리전 서비스 (VPC 밖) |
| S3 Gateway Endpoint | Lambda·워커 노드가 NAT 없이 S3 API 호출 | 프라이빗 컴퓨팅/데이터 서브넷 라우팅 테이블에 연결 |
| Lambda(`xray-s3-file-recorder`) | S3 `xray/` 업로드 이벤트를 받아 RDS `s3_file`에 멱등 기록 (입력·출력 파일 전부, 이미 구현 완료) | 프라이빗 컴퓨팅 서브넷 (VPC 연결) |
| CodeCommit/CodePipeline/CodeBuild | CI/CD | VPC 밖(관리형) |
| ECR | 컨테이너 이미지 저장소 (4개: `conservation-backend`/`conservation-guide-ai`/`xray-ai`/`pottery-inspection-ai`) | VPC 밖(관리형) |

> **EFS 없음**: 2차까지는 `conservation-backend` ↔ `xray-ai`가 EFS(`/shared`)로 파일을 주고받았지만, X-ray 파이프라인이 S3 + RDS 기반으로 재작성되면서 이 공유 볼륨 자체가 불필요해졌다. 그래서 이 문서에는 EFS 생성/CSI 드라이버/PV·PVC 단계가 없다.

### 전체 흐름
```
[일반 API 호출]
사용자 → Route53 → IGW → ALB → EC2(워커노드의 conservation-backend Pod)
                                     ↓ (내부 호출)
                    conservation-guide-ai / pottery-inspection-ai
                                     ↓
                                   RDS(Primary, Multi-AZ)

[X-ray 결합 — S3가 정본]
FE → conservation-backend  POST /api/xray/stitch/jobs/prepare (presigned PUT URL 발급)
FE → S3                    컬러 기준 이미지 + X-ray 조각 이미지 직접 업로드
S3 → Lambda(xray-s3-file-recorder) → RDS s3_file  (업로드 사실 기록, 멱등)
FE → conservation-backend  POST /jobs/{id}/start
conservation-backend → xray-ai(FastAPI)  입력 GET URL + 출력 PUT URL + 콜백 URL 전달
xray-ai(FastAPI) → S3      내려받아 /tmp에서 결합 → 결과(assembled/layout/report 등) 업로드
xray-ai(FastAPI) → conservation-backend  콜백 → RDS xray_job 상태 갱신
(콜백이 유실돼도 POST /jobs/{id}/reconcile로 S3 산출물을 보고 상태 복구 가능)

Dev Team → CodeCommit → CodePipeline → CodeBuild → ECR → (EKS가 이미지 pull)
```

---

## 1단계 — 커스텀 VPC 설계 및 생성

### 서브넷 설계 (VPC CIDR `10.0.0.0/16`)
| 서브넷 | AZ | CIDR | 용도 |
|---|---|---|---|
| `public-a` | AZ-A | `10.0.0.0/24` | NAT Gateway, ALB 노드 |
| `public-c` | AZ-C | `10.0.1.0/24` | NAT Gateway, ALB 노드 |
| `private-compute-a` | AZ-A | `10.0.10.0/24` | EKS 워커 노드, Lambda ENI |
| `private-compute-c` | AZ-C | `10.0.11.0/24` | EKS 워커 노드, Lambda ENI |
| `private-data-a` | AZ-A | `10.0.20.0/24` | RDS(Primary or Standby) |
| `private-data-c` | AZ-C | `10.0.21.0/24` | RDS(Primary or Standby) |

### VPC 생성
VPC 콘솔 → **VPC 생성** → "VPC 등"(VPC and more) 마법사보다는, 서브넷마다 태그(뒤에서 ALB 컨트롤러가 씀)를 세밀하게 다뤄야 하므로 **VPC만 먼저 생성** 후 서브넷/게이트웨이를 하나씩 만드는 걸 추천한다.

1. **VPC**: 이름 `conservation-vpc`, IPv4 CIDR `10.0.0.0/16`
2. **서브넷 6개**: 위 표대로 VPC 콘솔 → 서브넷 → **서브넷 생성**에서 반복 생성 (VPC는 `conservation-vpc` 고정, 각각 이름/AZ/CIDR만 표대로)
3. **인터넷 게이트웨이**: VPC 콘솔 → 인터넷 게이트웨이 → 생성(`conservation-igw`) → **VPC에 연결** → `conservation-vpc` 선택

### 서브넷 태그 (중요 — 나중에 ALB Ingress가 서브넷을 자동으로 못 찾는 문제로 이어짐)
AWS Load Balancer Controller(7단계)와 EKS 둘 다 "이 클러스터가 쓸 서브넷이 어디인지"를 **태그로** 찾는다. 6개 서브넷 모두에 아래 태그를 추가한다(VPC 콘솔 → 서브넷 → 태그 탭):

| 태그 키 | 값 | 대상 |
|---|---|---|
| `kubernetes.io/cluster/conservation-cluster` | `shared` | 6개 서브넷 전부 |
| `kubernetes.io/role/elb` | `1` | `public-a`, `public-c`만 |
| `kubernetes.io/role/internal-elb` | `1` | `private-compute-a`, `private-compute-c`만 |

### 라우팅 테이블
1. **퍼블릭 라우팅 테이블**(`public-rt`): 생성 → `conservation-vpc` → 라우팅 편집: `0.0.0.0/0 → conservation-igw` → 서브넷 연결: `public-a`, `public-c`
2. **NAT Gateway 2개**: VPC 콘솔 → NAT 게이트웨이 → 생성
   - `nat-a`: 서브넷 `public-a`, 탄력적 IP 새로 할당
   - `nat-c`: 서브넷 `public-c`, 탄력적 IP 새로 할당
   (AZ마다 따로 만드는 이유: 한 AZ가 죽어도 다른 AZ의 프라이빗 서브넷이 인터넷 경로를 잃지 않게 하기 위함 — 하나로 합치면 그 AZ 장애 시 다른 AZ까지 같이 영향받는다.)
3. **프라이빗 라우팅 테이블 2개**(AZ마다 별도, NAT도 AZ마다 다르므로):
   - `private-a-rt`: `0.0.0.0/0 → nat-a` / 서브넷 연결: `private-compute-a`, `private-data-a`
   - `private-c-rt`: `0.0.0.0/0 → nat-c` / 서브넷 연결: `private-compute-c`, `private-data-c`

여기까지 하면 VPC 준비가 끝난다. `<VPC ID>`를 적어둔다.

---

## 2단계 — 사전 준비 (CLI용 IAM 사용자)

2차와 동일하다. 이미 `conservation-cloud-deployer`(`AdministratorAccess`)를 만들어뒀다면 재사용하면 되고, 없다면 2차 문서 1단계 그대로 진행한다.

```bash
aws configure
# Access Key / Secret / region: ap-northeast-2 / output: json
```

---

## 3단계 — IAM 역할 준비

2차와 동일하게 `eksClusterRole`, `eksNodeRole` 2개가 필요하다(이미 있다면 재사용). 이번엔 추가로 아래 2개가 더 필요한데, 각각 해당 단계에서 만든다.

| 역할 | 용도 | 만드는 단계 |
|---|---|---|
| `eksClusterRole` | EKS 컨트롤 플레인 | 여기(2차와 동일) |
| `eksNodeRole` | 워커 노드(EC2) | 여기(2차와 동일) |
| `AmazonEKSLoadBalancerControllerRole` | ALB Ingress Controller Pod | 7단계 |
| `xray-s3-file-recorder-role` | Lambda(`xray-s3-file-recorder`) 실행 역할 | 14단계 |

`eksClusterRole`/`eksNodeRole` 생성 방법은 2차 문서 3단계와 완전히 동일하므로 여기서는 생략한다(신뢰할 수 있는 엔티티: `AWS 서비스` → EKS/EC2, 필요한 관리형 정책도 동일).

---

## 4단계 — EKS 클러스터 + 노드 그룹 생성 (커스텀 VPC 위에)

### 클러스터 생성
EKS 콘솔 → 클러스터 생성 → **`Custom configuration`**

**Configure cluster**: Name `conservation-cluster` / Cluster service role `eksClusterRole`

**Specify networking**:
| 항목 | 값 |
|---|---|
| VPC | `conservation-vpc` (1단계에서 만든 것, 기본 VPC 아님) |
| Subnets | **6개 전부 선택** (컨트롤 플레인 ENI가 여러 서브넷에 걸쳐 있어야 함) |
| Cluster endpoint access | `Public` |

나머지(애드온 등)는 2차와 동일하게 기본값 유지. 생성에 10~15분.

### 노드 그룹 추가
클러스터 `Active` 이후 컴퓨팅 탭 → 노드 그룹 추가:

| 항목 | 값 |
|---|---|
| Name | `workers` |
| Node IAM role | `eksNodeRole` |
| Instance type | `c7i-flex.large` (2차에서 `t3.medium`이 프리티어 조건에 안 맞아 실패했던 계정이면 이걸로. 계정 조건에 따라 다를 수 있으니 안 되면 다른 타입으로 시도) |
| Desired / Min / Max | 2 / 1 / 4 |
| **Subnets** | **`private-compute-a`, `private-compute-c`만 선택** (퍼블릭/데이터 서브넷은 선택하지 않는다 — 워커 노드가 퍼블릭 서브넷에 뜨면 프라이빗 컴퓨팅 서브넷이라는 설계 의도가 깨진다) |

### 확인
```bash
aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2
kubectl get nodes -o wide
```
`NODE` 항목의 내부 IP가 `10.0.10.x`/`10.0.11.x` 대역(프라이빗 컴퓨팅 서브넷)인지 확인한다.

---

## 5단계 — kubectl 로컬 연동 (Access Entry)

2차와 동일한 문제(`AdministratorAccess`가 있어도 `kubectl`이 인증 에러를 낼 수 있음)가 그대로 재현된다. 콘솔에서 클러스터를 만든 신원과 CLI IAM 사용자가 다르면:

EKS 콘솔 → 클러스터 → **액세스** 탭 → **IAM 액세스 항목 생성**
| 항목 | 값 |
|---|---|
| IAM 주체 | `conservation-cloud-deployer` |
| 액세스 정책 | `AmazonEKSClusterAdminPolicy` |

1~2분 후 `kubectl get nodes`로 재확인.

---

## 6단계 — OIDC 자격 증명 공급자 연동

2차 6단계와 동일. EKS 콘솔 → 클러스터 개요 → OIDC URL 확인 → IAM 콘솔 → ID 제공업체 → 공급자 추가(유형 `OpenID Connect`, 대상 `sts.amazonaws.com`).

이번엔 이 OIDC 공급자를 **AWS Load Balancer Controller(7단계)**에서 재사용한다. (Lambda 실행 역할은 VPC 관련 정책만 있으면 되므로 OIDC 신뢰 관계가 필요 없다 — 14단계 참고.)

---

## 7단계 — AWS Load Balancer Controller 설치 (ALB Ingress)

2차 "남은 작업" 1번(ALB Ingress Controller)을 여기서 실제로 설치한다. 콘솔만으로는 안 되고 **Helm**이 필요하다(이 프로젝트에서 Helm을 쓰는 유일한 지점).

### IAM 정책 생성
1. AWS Load Balancer Controller 공식 IAM 정책 JSON을 받는다:
   ```bash
   curl -O https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.9.0/docs/install/iam_policy.json
   ```
2. IAM 콘솔 → 정책 → 정책 생성 → JSON 탭에 위 파일 내용 붙여넣기 → 이름 `AWSLoadBalancerControllerIAMPolicy`

### IRSA 역할 생성
IAM 콘솔 → 역할 생성:
1. 신뢰할 수 있는 엔티티: `웹 자격 증명` → 6단계 OIDC 공급자 선택 → 대상 `sts.amazonaws.com`
2. 권한 정책: 방금 만든 `AWSLoadBalancerControllerIAMPolicy`
3. 이름: `AmazonEKSLoadBalancerControllerRole`
4. 생성 후 **신뢰 관계** 탭에서 `Condition`을 서비스 계정 단위로 제한(특정 네임스페이스·서비스 계정만 이 역할을 쓸 수 있도록 좁히는 패턴 — Pod 전체가 아니라 지정된 `ServiceAccount`만 assume 가능하게 함):
   ```json
   "StringEquals": {
     "oidc.eks.ap-northeast-2.amazonaws.com/id/<OIDC ID>:sub": "system:serviceaccount:kube-system:aws-load-balancer-controller",
     "oidc.eks.ap-northeast-2.amazonaws.com/id/<OIDC ID>:aud": "sts.amazonaws.com"
   }
   ```

### 서비스 계정 생성 + Helm 설치
```bash
kubectl create serviceaccount aws-load-balancer-controller -n kube-system
kubectl annotate serviceaccount aws-load-balancer-controller -n kube-system \
  eks.amazonaws.com/role-arn=arn:aws:iam::815373273907:role/AmazonEKSLoadBalancerControllerRole

helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=conservation-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set region=ap-northeast-2 \
  --set vpcId=<VPC ID>
```

### 확인
```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
```
`AVAILABLE`이 1 이상이면 완료. (Ingress 리소스 자체는 12단계에서 만든다 — 지금은 컨트롤러만 설치된 상태.)

---

## 8단계 — RDS(PostgreSQL, Multi-AZ) 생성

### 데이터베이스 생성
RDS 콘솔 → 데이터베이스 생성

| 항목 | 값 |
|---|---|
| 생성 방식 | 표준 생성 |
| 엔진 | PostgreSQL |
| **가용성 및 내구성** | **다중 AZ 배포(대기 인스턴스 포함)** — 2차는 Single-AZ였던 지점, 여기가 핵심 변경점 |
| DB 인스턴스 식별자 | `conservation-db` |
| 마스터 사용자 이름 | `conservation` |
| 마스터 암호 | `.env.k8s`에 쓸 값과 동일하게 |
| 인스턴스 클래스 | `db.t3.micro` |
| VPC | `conservation-vpc` |
| **서브넷 그룹** | 새로 생성 → `private-data-a`, `private-data-c` 포함하는 서브넷 그룹 지정 |
| 퍼블릭 액세스 | 아니오 |
| VPC 보안 그룹 | 새로 생성(`conservation-db-sg`) |
| 초기 데이터베이스 이름 | `conservation` |

> **비용 주의**: Multi-AZ는 프리티어 대상이 아니다. Single-AZ `db.t3.micro`는 프리티어로 월 750시간 무료지만, Multi-AZ는 Primary+Standby 두 인스턴스 요금이 청구된다(대략 Single-AZ의 2배 수준). 학습/데모 목적이라면 이 부분만 Single-AZ로 낮추는 것도 고려할 것 — 그 경우 아키텍처 다이어그램의 "Multi-AZ" 표시와는 달라진다는 점만 인지하고 진행.

### 보안 그룹 — Postgres(5432) 허용
DB 상세 → 연결 및 보안 → 보안 그룹 → 인바운드 규칙 추가: 유형 `PostgreSQL` / 소스 = 워커 노드 보안 그룹 (+ 14단계에서 만들 **Lambda 보안 그룹**도 여기 추가해야 함, 지금은 아직 없으니 14단계에서 다시 돌아와서 추가)

### `conservation` 데이터베이스 존재 확인
2차 8단계와 동일한 방법(임시 psql Pod)으로 확인 — Multi-AZ여도 접속 엔드포인트는 Primary를 가리키는 단일 엔드포인트라 절차는 동일하다.
```bash
kubectl run psql-tmp --rm -it --image=postgres:16 --restart=Never -- \
  psql "postgresql://conservation:<마스터 암호>@<엔드포인트>:5432/postgres"
```
`\l`로 `conservation` 있는지 확인, 없으면 `CREATE DATABASE conservation;`.

> **X-ray 테이블(`xray_job`/`xray_defect`/`s3_file`) 생성 순서**: `conservation-backend`는 `spring.jpa.hibernate.ddl-auto=update`로 떠 있어서(`application.yaml`) JPA 엔티티 기준으로 테이블을 자동 생성한다. 즉 12~13단계에서 `conservation-backend`를 최소 1회 기동시키면 이 테이블들이 자동으로 만들어진다. **14단계(Lambda 연결)는 반드시 그 이후에 진행할 것** — `s3_file` 테이블이 없는 상태에서 Lambda가 먼저 이벤트를 받으면 기록에 실패한다. 순서를 앞당기고 싶으면 `db/xray_s3_rds.sql`을 위 psql Pod에서 직접 실행해 수동으로 먼저 만들어도 된다.
>
> **실전에서 발견된 함정**: `S3FileRecord.java`(JPA 엔티티)의 `created_at`/`updated_at`은 `@Column(nullable = false)`만 있고 DB 레벨 기본값 지정이 없어서, `ddl-auto=update`로 생성된 실제 테이블에는 `DEFAULT CURRENT_TIMESTAMP`가 안 붙는다. 반면 Lambda(`repository.py`)는 새 행을 넣을 때 이 두 컬럼을 아예 안 채우고 DB 기본값에 의존하도록 짜여 있어서, 이 상태 그대로면 Lambda가 `s3_file`에 처음 쓰기를 시도할 때 `NotNullViolation`으로 실패한다. `conservation-backend`를 처음 띄운 뒤(12~13단계) 아래를 한 번 실행해서 미리 막아두는 걸 권장한다:
> ```sql
> ALTER TABLE public.s3_file ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
> ALTER TABLE public.s3_file ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;
> ```

---

## 9단계 — ECR 리포지토리 생성 + 이미지 최초 push

2차 2단계와 동일하되, 이번엔 리포지토리가 **4개**다(앱 4개). Lambda(`xray-s3-file-recorder`)는 컨테이너 이미지가 아니라 **zip 패키지**로 배포하므로 ECR 리포지토리가 필요 없다(14단계 참고).

| Repository name | Dockerfile 위치 |
|---|---|
| `conservation-backend` | 레포 루트 `/Dockerfile` |
| `conservation-guide-ai` | `/ai-services/conservation-guide-ai/Dockerfile` |
| `xray-ai` | `/ai-services/xray-ai/Dockerfile` |
| `pottery-inspection-ai` | `/ai-services/pottery-inspection-ai/Dockerfile` |

CI/CD(15단계)가 갖춰지기 전까지, 최초 1회는 2차와 동일하게 로컬에서 수동으로 build/tag/push 해서 EKS가 최소한 뜰 수 있는 상태를 만든다:
```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com

docker build -t conservation-backend .
docker tag conservation-backend:latest 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
docker push 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
# 나머지 3개(conservation-guide-ai, xray-ai, pottery-inspection-ai)도 각자 디렉터리에서 동일 패턴 반복
```

---

## 10단계 — `pottery-inspection-ai` 전용 준비 (재사용)

2차 11단계에서 이미 정리된 내용 그대로다 — `ai_hub/pottery_multitask_model_v2`는 git에 커밋돼 있으므로 `Dockerfile`의 `COPY ai_hub/pottery_multitask_model_v2 ./ai_hub/pottery_multitask_model_v2`와 `.dockerignore`의 `ai_hub/*` + `!ai_hub/pottery_multitask_model_v2` 예외 처리가 이미 코드에 반영되어 있다면 이 단계는 확인만 하고 넘어가면 된다.

---

## 11단계 — Secret 생성 (`app-secrets`)

2차 12단계와 동일한 방식이되, **main에 새로 병합된 JWT 인증 기능**과 **X-ray S3 파이프라인**(PR #22) 때문에 키가 추가된다. `.env.k8s`에 RDS 엔드포인트(8단계에서 새로 만든 것), S3 버킷 이름(14단계에서 만들 버킷), JWT 시크릿, 콜백 토큰 등을 채운다.

> **S3 버킷 이름을 아직 안 정했다면**: 버킷 생성 자체(14-1)는 Lambda 배포와 무관하게 독립적으로 먼저 해둬도 된다. S3 콘솔에서 이름만 미리 만들어두고(`conservation-xray-uploads-815373273907` 등), 나머지 Lambda 배선(14-2~14-8)은 나중에 14단계에서 이어서 하면 된다.

```env
OPENAI_API_KEY=<값>
POSTGRES_PASSWORD=<값>
AWS_ACCESS_KEY_ID=<값>
AWS_SECRET_ACCESS_KEY=<값>
AWS_S3_BUCKET=<14-1에서 만들 버킷 이름>
AWS_REGION=ap-northeast-2

SPRING_DATASOURCE_URL=jdbc:postgresql://<RDS 엔드포인트>:5432/conservation
SPRING_DATASOURCE_USERNAME=conservation
DATABASE_URL=postgresql://conservation:<POSTGRES_PASSWORD 실제값>@<RDS 엔드포인트>:5432/conservation

JWT_SECRET=<최소 32바이트(256비트) 이상 랜덤 문자열>
JWT_EXPIRATION_MS=3600000

XRAY_STITCH_CALLBACK_TOKEN=<임의의 랜덤 토큰, 예: openssl rand -hex 20>
```

> **새로 생긴 필수값(주의)**: `WebSecurityConfig`/`JwtTokenProvider`가 `jwt.secret_key: ${JWT_SECRET}`를 읽어서 `Keys.hmacShaKeyFor(...)`(HS256)로 서명 키를 만든다. 이 값이 없으면 컨테이너가 아예 기동에 실패하고(플레이스홀더 해석 오류), 있어도 **32바이트(256비트)보다 짧으면 로그인 시 `WeakKeyException`이 난다** — 랜덤 문자열을 생성할 때(`openssl rand -base64 32` 등) 이 최소 길이를 지킬 것. `JWT_EXPIRATION_MS`는 `docker-compose.yml`에는 전달되지만 실제 만료 시간은 `application.yaml`의 `jwt.expiration-ms: 3600000`(1시간, 하드코딩)이 적용되므로 값 자체는 크게 중요하지 않다 — 그래도 다른 env 값들과의 일관성을 위해 채워는 둔다.
>
> **`XRAY_STITCH_CALLBACK_TOKEN`이 왜 필요한가**: `conservation-backend`가 `xray-ai`(FastAPI)에 결합 작업을 넘길 때 콜백 URL과 함께 이 토큰을 같이 보낸다. FastAPI는 작업이 끝나면 이 토큰을 `X-Xray-Callback-Token` 헤더에 실어 `conservation-backend`의 `/api/xray/stitch/callback`을 호출하고, `conservation-backend`는 헤더 값이 이 값과 일치하는지 확인한다(`application.yaml`의 `xray.stitch.callback-token`). 값을 비워두면(기본값) 검증 자체를 건너뛰므로, 운영 환경에서는 반드시 채워야 한다.

```bash
kubectl create secret generic app-secrets --from-env-file=.env.k8s
```

> `.env.k8s`를 나중에 고칠 때마다(비밀번호 변경 등) **로컬 파일만 고치는 걸로는 반영 안 된다** — `kubectl delete secret app-secrets && kubectl create secret generic app-secrets --from-env-file=.env.k8s` 후 관련 Deployment `rollout restart`까지 해야 한다(2차에서 직접 겪은 문제).

---

## 12단계 — Deployment/Service/Ingress 매니페스트 작성

> **주의**: 지금 레포에 있는 `k8s/app.yaml`은 아직 2차(EFS 기반) 버전 그대로다. X-ray가 S3+RDS 기반으로 바뀌면서 `xray-ai`/`conservation-backend`의 환경변수와 볼륨 설정이 달라졌으므로, 아래 내용으로 **`k8s/app.yaml`을 직접 교체**해야 한다. `k8s/storage.yaml`(EFS StorageClass/PV/PVC)은 더 이상 `apply`하지 않는다.

2차 대비 바뀌는 지점을 요약하면:
- `xray-ai`: `shared-storage` 볼륨 마운트 제거, `XRAY_STITCH_JOBS_ROOT`/`XRAY_STITCH_CACHE_DIR`를 `/tmp` 경로로 변경(컨테이너 재시작 시 사라져도 되는 임시 작업공간 — S3/RDS가 정본).
- `conservation-backend`: `shared-storage` 볼륨 마운트 제거, 더 이상 쓰지 않는 `XRAY_STORAGE_LOCAL_ROOT`/`XRAY_STORAGE_CONTAINER_ROOT` 삭제, 대신 `XRAY_STITCH_CALLBACK_URL`을 추가(자기 자신을 가리키는 k8s 내부 DNS). `AWS_S3_BUCKET`과 `XRAY_STITCH_CALLBACK_TOKEN`은 `app-secrets`의 `envFrom`으로 자동 주입되므로 여기 따로 안 적어도 된다.
- `conservation-backend`의 Service는 2차처럼 `type: LoadBalancer`로 바꾸지 않는다. **`type: ClusterIP`로 그대로 두고**, 대신 `Ingress`를 추가한다(ALB는 Ingress가 대신 만들어준다).

`k8s/app.yaml` 전체 내용:

```yaml
# 계정ID: 815373273907, 리전: ap-northeast-2
apiVersion: apps/v1
kind: Deployment
metadata:
  name: conservation-guide-ai
spec:
  replicas: 1
  selector:
    matchLabels:
      app: conservation-guide-ai
  template:
    metadata:
      labels:
        app: conservation-guide-ai
    spec:
      containers:
        - name: conservation-guide-ai
          image: 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-guide-ai:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: app-secrets
---
apiVersion: v1
kind: Service
metadata:
  name: conservation-guide-ai
spec:
  selector:
    app: conservation-guide-ai
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xray-ai
spec:
  replicas: 1
  selector:
    matchLabels:
      app: xray-ai
  template:
    metadata:
      labels:
        app: xray-ai
    spec:
      containers:
        - name: xray-ai
          image: 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/xray-ai:latest
          ports:
            - containerPort: 8000
          env:
            - name: XRAY_DEVICE
              value: "cpu"
            - name: XRAY_DEFAULT_CONF
              value: "0.08"
            - name: XRAY_NMS_IOU
              value: "0.5"
            - name: XRAY_STITCH_JOBS_ROOT
              value: "/tmp/xray_jobs"
            - name: XRAY_STITCH_ENGINE_DIR
              value: "/code"
            - name: XRAY_STITCH_SINGLE_SCRIPT
              value: "/code/scripts/assemble_xray.py"
            - name: XRAY_STITCH_BATCH_SCRIPT
              value: "/code/scripts/batch_assemble.py"
            - name: XRAY_STITCH_CONFIG_DIR
              value: "/code/configs"
            - name: XRAY_STITCH_MAPPING_DIR
              value: "/code/mappings"
            - name: XRAY_STITCH_MAPPING_PATH
              value: "/code/mappings/mapping.color_front.json"
            - name: XRAY_STITCH_CACHE_DIR
              value: "/tmp/xray_cache"
          envFrom:
            - secretRef:
                name: app-secrets
---
apiVersion: v1
kind: Service
metadata:
  name: xray-ai
spec:
  selector:
    app: xray-ai
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pottery-inspection-ai
spec:
  replicas: 1
  selector:
    matchLabels:
      app: pottery-inspection-ai
  template:
    metadata:
      labels:
        app: pottery-inspection-ai
    spec:
      containers:
        - name: pottery-inspection-ai
          image: 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/pottery-inspection-ai:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: app-secrets
          volumeMounts:
            - name: hf-cache
              mountPath: /root/.cache/huggingface
      volumes:
        - name: hf-cache
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: pottery-inspection-ai
spec:
  selector:
    app: pottery-inspection-ai
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: conservation-backend
spec:
  replicas: 1
  selector:
    matchLabels:
      app: conservation-backend
  template:
    metadata:
      labels:
        app: conservation-backend
    spec:
      containers:
        - name: conservation-backend
          image: 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
          ports:
            - containerPort: 8080
          env:
            - name: CONSERVATION_GUIDE_AI_BASE_URL
              value: "http://conservation-guide-ai:8000"
            - name: POTTERY_INSPECTION_AI_BASE_URL
              value: "http://pottery-inspection-ai:8000"
            - name: XRAY_AI_BASE_URL
              value: "http://xray-ai:8000"
            - name: XRAY_AI_TIMEOUT_SECONDS
              value: "300"
            - name: XRAY_AI_CONFIG_NAME
              value: "config.batch_fast.color_slot_voronoi_all_fragments_v13_conservative.json"
            - name: XRAY_STITCH_CALLBACK_URL
              value: "http://conservation-backend:8080/api/xray/stitch/callback"
            - name: SPRING_DATASOURCE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: POSTGRES_PASSWORD
          envFrom:
            - secretRef:
                name: app-secrets
      # shared-storage(EFS) 볼륨 없음 — X-ray 산출물은 S3에 저장되고
      # 이 Pod의 로컬 디스크는 쓰지 않는다.
---
apiVersion: v1
kind: Service
metadata:
  name: conservation-backend
spec:
  selector:
    app: conservation-backend
  ports:
    - port: 8080
      targetPort: 8080
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: conservation-backend
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}]'
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

(`alb.ingress.kubernetes.io/scheme: internet-facing`이 "퍼블릭 서브넷에 ALB 노드를 만들어라"는 뜻이고, `target-type: ip`는 "NodePort를 거치지 않고 Pod IP로 바로 라우팅해라"는 뜻 — 이게 2차의 `type: LoadBalancer` 방식보다 실제 트래픽 경로가 한 홉 짧다.)

---

## 13단계 — 적용 및 검증

```bash
kubectl apply -f k8s/app.yaml
kubectl get pods -w
```

트러블슈팅은 2차 14단계 표와 동일하게 적용된다(요약):

| 증상 | 원인 후보 | 확인 |
|---|---|---|
| `conservation-guide-ai` 계속 재시작 | RDS 보안 그룹 5432 인바운드 누락 | `kubectl logs <pod> --previous`에서 `PoolTimeout` |
| `conservation-backend`가 뜨자마자 바로 `Error` | RDS 마스터 암호 ≠ Secret의 `POSTGRES_PASSWORD` | `FATAL: password authentication failed` |
| 비밀번호 고쳤는데도 여전히 `Error` | Secret 재생성 안 함 | `kubectl delete secret` + 재생성 + `rollout restart` |
| `database "conservation" does not exist` | DB 미생성 | 8단계 psql Pod로 `CREATE DATABASE` |
| `conservation-backend`가 시작하자마자 `Error`(플레이스홀더 관련 예외) | `app-secrets`에 `JWT_SECRET` 누락 (11단계 참고) | `kubectl logs <pod> --previous`에서 `Could not resolve placeholder 'JWT_SECRET'` 류 메시지 확인 |
| X-ray `/jobs/prepare`는 되는데 `/start`에서 409/S3 관련 에러 | S3 버킷 권한 부족 또는 `AWS_S3_BUCKET` 오타 | `kubectl logs <pod>`에서 S3 관련 예외 메시지 확인, IAM 자격증명 재점검 |

### Ingress(ALB) 상태 확인
```bash
kubectl get ingress conservation-backend
```
`ADDRESS` 칸에 ALB 도메인이 뜨기까지 1~3분 걸린다. 계속 비어있으면 `kubectl describe ingress conservation-backend`의 Events에서 원인을 본다 — 대부분 1단계의 서브넷 태그 누락(`kubernetes.io/role/elb` 등) 아니면 7단계 컨트롤러 IAM 정책 문제다.

---

## 14단계 — S3 + Lambda: X-ray 업로드 기록 (`xray-s3-file-recorder`)

> 이 기능은 이미 main에 구현되어 있다(`aws/lambda/xray-s3-file-recorder/`, `docs/XRAY_S3_RDS_INTEGRATION.md`). 별도 TODO 없이 코드 그대로 배포하면 된다. 자세한 배포/장애 대응은 `aws/lambda/xray-s3-file-recorder/README_LAMBDA.md`가 이 Lambda 전용 문서이니 함께 참고할 것.

### 14-1. S3 버킷 생성
S3 콘솔 → 버킷 만들기 → 이름(전역 유일해야 함, 예: `conservation-xray-uploads-815373273907`) → 리전 `ap-northeast-2` → 퍼블릭 액세스 차단 유지(기본값).

이 버킷 아래 `xray/` 프리픽스에 아래 규칙으로 파일이 쌓인다(`XrayS3Keys.java`/`s3_key.py` 기준):
```
xray/{artifactId}/inputs/color/{fileName}
xray/{artifactId}/inputs/xray/{fileName}

xray/{artifactId}/outputs/assembled_xray.png
xray/{artifactId}/outputs/layout.json
xray/{artifactId}/outputs/report.json
xray/{artifactId}/outputs/layout_fragment_masks.zip
xray/{artifactId}/outputs/layout.final.json
xray/{artifactId}/outputs/assembled_xray.final.png
xray/{artifactId}/outputs/source_owner.final.png
xray/{artifactId}/outputs/fragment_owner.final.png
xray/{artifactId}/outputs/seam_zone.final.png
xray/{artifactId}/outputs/overlap_mask.final.png
xray/{artifactId}/outputs/provenance.final.json
xray/{artifactId}/outputs/defect_result.png
```

**CORS 설정(FE가 브라우저에서 presigned URL로 직접 PUT/GET 하려면 필수)**: FE는 Spring을 거치지 않고 브라우저에서 **S3에 직접** 업로드/다운로드한다(11-14단계 흐름 참고). 이때 브라우저가 cross-origin 요청을 보내는데, S3 버킷에 CORS 설정이 없으면 브라우저가 `Access-Control-Allow-Origin` 헤더 없음으로 요청 자체를 막아버린다 — `WebConfig.java`의 `app.cors.allowed-origins`(Spring 자체의 CORS 설정)와는 완전히 별개이므로 반드시 버킷에도 따로 설정해야 한다.

S3 콘솔 → 버킷 → **권한** 탭 → 맨 아래 **CORS** 섹션 → 편집 → 아래 JSON 붙여넣기:
```json
[
    {
        "AllowedOrigins": [
            "http://localhost:5173",
            "http://localhost:5174",
            "http://localhost:3000"
        ],
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3000
    }
]
```
`AllowedHeaders`를 와일드카드로 둔 이유는 presigned PUT URL의 `X-Amz-SignedHeaders`에 `x-amz-meta-usage`/`x-amz-meta-original_name` 같은 커스텀 메타데이터 헤더가 포함돼 있어서다(Spring이 `XrayS3Service.presignInputPut`에서 메타데이터를 실어 보내는 방식). FE를 실제 도메인으로 배포하면 그 도메인도 `AllowedOrigins`에 추가해야 한다.

### 14-2. S3 Gateway VPC Endpoint 생성
Lambda가 VPC 안에 들어가면 기본 인터넷 경로를 잃으므로, NAT를 안 거치고 S3 API(`head_object`)를 호출하려면 이 엔드포인트가 필요하다.

VPC 콘솔 → 엔드포인트 → 엔드포인트 생성
| 항목 | 값 |
|---|---|
| 서비스 카테고리 | AWS 서비스 |
| 서비스 이름 | `com.amazonaws.ap-northeast-2.s3` (**Gateway** 타입인 것 확인) |
| VPC | `conservation-vpc` |
| 라우팅 테이블 | `private-a-rt`, `private-c-rt` 체크 (프라이빗 컴퓨팅/데이터 서브넷이 쓰는 라우팅 테이블) |
| 정책 | 전체 액세스(기본값) |

### 14-3. Lambda용 보안 그룹
EC2 콘솔 → 보안 그룹 생성: 이름 `xray-s3-file-recorder-sg`, VPC `conservation-vpc`, 인바운드 규칙 없음(아웃바운드는 기본 전체 허용 유지).

### 14-4. RDS 보안 그룹에 Lambda 추가 (8단계로 돌아가서)
`conservation-db-sg` 인바운드 규칙 추가: 유형 `PostgreSQL` / 소스 = `xray-s3-file-recorder-sg` (드롭다운에서 선택).

### 14-5. Lambda 실행 역할
IAM 콘솔 → 역할 생성 → 서비스: Lambda

두 가지 권한이 필요하다(용도가 다르므로 둘 다 부여):
1. **`AWSLambdaVPCAccessExecutionRole`**(관리형 정책) — VPC 안에서 ENI 생성/삭제 + CloudWatch Logs 쓰기. RDS에 접속하려면 필수.
2. **S3 `GetObject` 인라인 정책** — Lambda가 `head_object`로 업로드된 파일의 메타데이터(`usage`/`source_order`/`original_name`)를 읽으려면 필요. 관리형 정책엔 없으므로 아래 JSON으로 인라인 정책을 추가한다(버킷 이름은 14-1에서 만든 것으로 교체):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject"],
         "Resource": "arn:aws:s3:::conservation-xray-uploads-815373273907/xray/*"
       }
     ]
   }
   ```

이름: `xray-s3-file-recorder-role`.

(S3 이벤트가 Lambda를 호출하는 권한은 IAM 역할이 아니라 Lambda의 **리소스 기반 정책**으로 별도 관리되며, 콘솔에서 트리거를 추가하면 자동으로 붙는다.)

### 14-6. 배포 ZIP 빌드
이 Lambda는 컨테이너 이미지가 아니라 **zip 패키지**로 배포한다(코드가 가볍고 `psycopg2-binary` 하나만 의존하므로 컨테이너보다 zip이 더 단순하다). Git Bash 또는 WSL에서:

```bash
cd aws/lambda/xray-s3-file-recorder
./build_zip.sh
```

`dist/xray-s3-file-recorder.zip`이 생성된다(Python 3.11 / Linux x86_64용 `psycopg2` + 소스 코드 포함, 빌드 시 PyPI 접근 필요).

### 14-7. Lambda 함수 생성
Lambda 콘솔 → 함수 생성 → **처음부터 작성** → 이름 `xray-s3-file-recorder` → 런타임 `Python 3.11` → 아키텍처 `x86_64` → 실행 역할: 기존 역할 사용 → `xray-s3-file-recorder-role`

생성 후:
- **코드** 탭 → 업로드 → `.zip 파일` → `dist/xray-s3-file-recorder.zip` 업로드 → 핸들러를 `lambda_function.lambda_handler`로 확인
- **구성** 탭:
  - **VPC**: `conservation-vpc`, 서브넷 `private-compute-a` + `private-compute-c` 둘 다, 보안 그룹 `xray-s3-file-recorder-sg`
  - **환경 변수**(필수): `DB_HOST`(RDS 엔드포인트), `DB_USER=conservation`, `DB_PASSWORD`(마스터 암호)
  - **환경 변수**(선택, 기본값 있음): `DB_PORT=5432`, `DB_NAME=conservation`, `DB_SCHEMA=public`, `S3_FILE_TABLE=s3_file`, `DB_SSLMODE=prefer`
  - **제한 시간**: 기본 3초는 짧을 수 있으니 10초 정도로 늘려두는 걸 권장

### 14-8. S3 트리거 연결
Lambda 콘솔 → 함수 → **트리거 추가** → S3 → 버킷: 14-1에서 만든 버킷 → 이벤트 유형: **모든 객체 생성 이벤트**(`s3:ObjectCreated:*`) → 접두사: `xray/` → 저장. (콘솔이 S3 버킷 알림 설정 + Lambda 리소스 기반 정책 부여를 자동으로 처리한다.)

### 확인
FE에서 X-ray 업로드를 실제로 진행하거나(권장), 콘솔에서 `xray/{테스트용 UUID}/inputs/color/test.png` 같은 키로 아무 이미지나 직접 업로드해본다. Lambda 콘솔 → **모니터링 → CloudWatch 로그**에서 `{"processed": 1, "ignored": 0, "errors": []}` 형태의 로그가 찍히는지 확인하고, RDS에서 직접 확인한다:
```sql
SELECT artifact_id, usage_name, source_order, original_name, s3_key, status, created_at
FROM public.s3_file
ORDER BY created_at DESC
LIMIT 5;
```
장애 대응이 더 필요하면 `aws/lambda/xray-s3-file-recorder/README_LAMBDA.md`의 "9. 장애 대응" 절을 참고한다.

---

## 15단계 — CI/CD 파이프라인 (CodeCommit → CodePipeline → CodeBuild → ECR)

2차 "남은 작업" 3번을 여기서 구현한다.

> **주의**: AWS CodeCommit은 2024년 7월부터 **신규 리포지토리 생성이 기존 사용 이력이 없는 계정에서는 막혀 있을 수 있다**(AWS가 신규 고객에게는 더 이상 CodeCommit을 권하지 않는 방향으로 정책을 바꿨다). 계정에서 CodeCommit 콘솔에 들어가서 "리포지토리 생성" 버튼이 비활성화되어 있거나 안내 문구만 뜬다면, 소스 저장소는 GitHub을 그대로 쓰고 **CodeStar Connections**로 CodePipeline과 연결하는 방식으로 대체해야 한다(그 경우 다이어그램의 CodeCommit 자리만 GitHub+CodeStar Connections로 바뀌고 뒤 단계는 동일). 진행 전에 콘솔에서 먼저 확인할 것.

### 15-1. CodeCommit 리포지토리 생성 (또는 위 대체 경로)
CodeCommit 콘솔 → 리포지토리 생성 → 이름 `conservation-backend` → 로컬 저장소에 원격 추가 후 push:
```bash
git remote add codecommit <CodeCommit에서 안내하는 clone URL>
git push codecommit main
```

### 15-2. CodeBuild 프로젝트 생성
CodeBuild 콘솔 → 빌드 프로젝트 생성
| 항목 | 값 |
|---|---|
| 프로젝트 이름 | `conservation-build` |
| 소스 | CodeCommit → `conservation-backend` 리포지토리, `main` 브랜치 |
| 환경 이미지 | Amazon Linux, 관리형 이미지 (표준 러너) |
| 권한 부여 | **특수 권한** 체크 (Docker 빌드 시 Docker-in-Docker가 필요) |
| 서비스 역할 | 새로 생성 → 생성 후 `AmazonEC2ContainerRegistryPowerUser` 정책 추가 부여 |
| Buildspec | 리포지토리의 `buildspec.yml` 사용 |

레포 루트에 `buildspec.yml` 작성:
```yaml
version: 0.2

env:
  variables:
    AWS_REGION: ap-northeast-2
    ACCOUNT_ID: "815373273907"

phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
      - IMAGE_TAG=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | cut -c 1-7)
  build:
    commands:
      - docker build -t conservation-backend:$IMAGE_TAG .
      - docker build -t conservation-guide-ai:$IMAGE_TAG ai-services/conservation-guide-ai
      - docker build -t xray-ai:$IMAGE_TAG ai-services/xray-ai
      - docker build -t pottery-inspection-ai:$IMAGE_TAG ai-services/pottery-inspection-ai
  post_build:
    commands:
      - |
        for svc in conservation-backend conservation-guide-ai xray-ai pottery-inspection-ai; do
          docker tag $svc:$IMAGE_TAG $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$svc:$IMAGE_TAG
          docker tag $svc:$IMAGE_TAG $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$svc:latest
          docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$svc:$IMAGE_TAG
          docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$svc:latest
        done
```

> Lambda(`xray-s3-file-recorder`)는 이 파이프라인 대상이 아니다 — 코드가 바뀌면 14-6/14-7을 다시 수행(zip 재빌드 + 콘솔에서 코드 업로드)해서 갱신한다. 자주 바뀌는 컴포넌트가 아니라서 지금 단계에서는 자동화하지 않는다.

### 15-3. (선택) EKS까지 자동 배포
`buildspec.yml`의 `post_build` 뒤에 아래를 더 붙이면 이미지 push까지뿐 아니라 실행 중인 Pod도 자동으로 새 이미지로 교체된다. 다만 이러려면 **CodeBuild의 서비스 역할에도 5단계와 같은 EKS Access Entry를 만들어줘야 한다**(`AmazonEKSClusterAdminPolicy`까지는 과하고, 배포용으로 범위를 좁힌 정책을 쓰는 걸 권장):
```yaml
      - curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
      - chmod +x kubectl && mv kubectl /usr/local/bin/
      - aws eks update-kubeconfig --name conservation-cluster --region $AWS_REGION
      - kubectl rollout restart deployment/conservation-backend deployment/conservation-guide-ai deployment/xray-ai deployment/pottery-inspection-ai
```
자동화가 아직 부담스럽다면 이 부분은 생략하고, 2차처럼 이미지 push까지만 자동화한 뒤 `kubectl rollout restart`는 계속 수동으로 해도 된다 — 그 경우 CI(빌드/푸시)까지만 자동, CD(배포)는 수동인 상태로 남는다는 걸 팀 안에서 인지하고 있으면 된다.

### 15-4. CodePipeline 생성
CodePipeline 콘솔 → 파이프라인 생성
| 스테이지 | 공급자 | 설정 |
|---|---|---|
| Source | CodeCommit | 리포지토리 `conservation-backend`, 브랜치 `main` |
| Build | CodeBuild | 프로젝트 `conservation-build` |

생성 후 `main`에 push할 때마다 파이프라인이 자동 실행된다.

---

## 16단계 — 외부 접속 확인

```bash
kubectl get ingress conservation-backend
```
`ADDRESS`에 뜬 ALB 도메인(`k8s-default-conservationbackend-xxxxxxxx.ap-northeast-2.elb.amazonaws.com` 형태)으로 접속한다. 2차와 달리:
- **포트를 안 붙여도 된다**: Ingress의 `listen-ports`를 80으로 지정했으므로 `http://<ALB 도메인>`으로 바로 접속 가능 (2차의 `:8080` 붙이던 것과 다른 지점).
- 루트(`/`)로 접속 시 여전히 404가 날 수 있다 — 실제 존재하는 경로(`/api/xray/health` 등)로 확인.

```bash
curl http://<ALB 도메인>/api/xray/health
```

---

## 17단계 — 비용 참고

2차 대비 새로 생기거나 늘어나는 과금 요소:
| 항목 | 비고 |
|---|---|
| NAT Gateway ×2 | 시간당 요금 + 처리한 데이터(GB)당 요금, 2차엔 아예 없던 항목 |
| RDS Multi-AZ | Single-AZ 대비 대략 2배 (Standby 인스턴스 비용) |
| ALB | 시간당 요금 + LCU(트래픽량) 요금 — 2차의 Service `LoadBalancer`(NLB류)와 요금 체계가 다름 |
| Lambda | 호출 횟수/실행시간 기준, 업로드 이벤트마다 소량 과금(프리티어 월 100만 건까지 무료라 이 프로젝트 규모에선 사실상 무료) |
| VPC Endpoint(S3, Gateway 타입) | 무료 |
| S3 | 저장 용량 + 요청 건수 기준, X-ray 원본/산출물이 쌓이는 만큼 완만히 증가 |

> **EFS가 없어서 더 단순해진 부분**: 이전 버전 문서는 EFS(파일 시스템 + 마운트 타깃 개수만큼 과금)도 비용 항목에 있었지만, X-ray가 S3 기반으로 바뀌면서 이 항목 자체가 사라졌다. S3는 EFS보다 저장 비용이 저렴하고, 쓴 만큼만 과금된다.

정지/축소 시 주의할 점(2차에서 다룬 것과 동일한 원리): EKS 노드 그룹을 0으로 줄여도 **NAT Gateway·ALB·RDS·Lambda는 별도로 끄지 않는 한 계속 과금 대상으로 남는다**(Lambda 자체는 호출이 없으면 과금이 거의 없지만, VPC에 연결된 상태는 유지된다). 완전히 멈추고 싶다면 노드 그룹 축소 + RDS 중지(최대 7일) + (필요시) NAT Gateway/ALB 자체를 삭제해야 한다 — 다만 NAT Gateway·서브넷·라우팅 테이블을 지우면 이 아키텍처를 다시 쓸 때 1단계부터 재구성해야 하니, 단기 중단이면 EKS 축소 + RDS 중지 정도로 충분하다.

---

## 18단계 — 남은 작업

1. **Lambda → EC2 알림 경로 재검토**: 지금은 Lambda가 RDS `s3_file`에 직접 쓰기만 한다. `xray-ai`/`conservation-backend`가 이 상태 변화를 실시간으로 알아야 한다면(폴링이 아니라 즉시 알림이 필요하다면) Lambda에서 ALB(내부 경로) 또는 내부 API 호출을 추가하는 걸 고려. (현재는 `conservation-backend`가 필요 시점에 S3 객체 존재 여부/`s3_file` 레코드를 직접 조회하는 방식으로 충분히 동작한다.)
2. **IRSA로 S3 접근 전환**: `conservation-backend`가 여전히 `.env.k8s`의 정적 `AWS_ACCESS_KEY_ID`/`SECRET`으로 S3에 접근 중이면, ServiceAccount 기반 IAM 역할로 전환 검토(`S3Config.java`는 `DefaultCredentialsProvider`도 지원하므로 정적 키를 비워두면 자동으로 IRSA를 타게 되어 있어 — 전환 자체는 이미 코드 레벨에서 가능한 상태).
3. **HTTPS**: 지금 ALB는 `HTTP` 80 포트만 연다. 도메인을 붙이고 ACM 인증서를 발급해서 `HTTPS` 리스너를 추가하는 게 다음 단계.
4. **CodeCommit 대체 경로 확정**: 15단계 서두의 주의사항대로, 계정에서 CodeCommit이 막혀 있다면 GitHub + CodeStar Connections로 소스 스테이지를 바꿔야 한다.

---

## 부록 A — 실전 배포 기록 (신규 계정 `428270342381`, 2026-08-08)

이 절은 위 1~13단계를 실제 신규 AWS 계정에서 처음부터 밟아본 기록이다. 문서 내용과 다르게 진행한 부분, 실제로 만난 에러와 해결 방법, 진행 중 나온 질문과 답을 단계별로 남긴다. 14단계 이후는 이 기록 시점까지 아직 진행하지 않았다.

**계정 정보**: IAM 사용자는 새로 만들지 않고 기존 `jhlee`(`AdministratorAccess`)를 재사용했다 — 문서에서 `conservation-cloud-deployer`라고 부르는 자리를 전부 `jhlee`로 치환해서 진행. 계정 ID `428270342381`, VPC ID `vpc-0fb6b7db0281d93b2`.

### 1단계 — VPC
문서 그대로 VPC + 서브넷 6개 + IGW + 태그 6세트 + 라우팅 테이블 3개 + NAT Gateway 2개를 만들었고, 특별한 이슈 없이 끝났다.

- **문서에 없던 UI 변화**: NAT Gateway 생성 화면에 "리전별-신규(모든 가용 영역에 자동 확장)"라는, 문서 작성 시점엔 없었던 옵션이 새로 생겨 있었다. 이건 리전에 있는 **모든** AZ(우리가 안 쓰는 AZ 포함)에 자동으로 NAT 엔드포인트를 펼치는 기능이라, 우리 아키텍처(AZ마다 독립된 NAT 1개씩, 정확히 2개)와 맞지 않는다. **"가용 영역"**(AZ 단위, 서브넷 직접 지정) 옵션을 선택해야 문서 의도대로 동작하고, 안 그러면 안 쓰는 AZ에까지 NAT가 생겨 불필요한 과금이 붙는다.
- **질문 — 서브넷을 왜 `/24`로 촘촘히 나누나?**: AZ 이중화(장애 시 다른 AZ로 트래픽 유지)와 용도별(퍼블릭/프라이빗-컴퓨팅/프라이빗-데이터) 보안 격리 때문에 6개로 쪼갠 것이고, `/24`(251개 사용 가능 IP)는 딱 맞춘 게 아니라 오히려 여유 있게 잡은 관용적 크기(대역 사이에 빈 공간도 남겨둠)라는 걸 설명했다.
- **질문 — 태그가 왜 `elb`인가(연결하는 건 ALB인데)?**: `elb`는 특정 로드밸런서 종류가 아니라 AWS의 "Elastic Load Balancing" 상품군 전체(Classic LB/ALB/NLB)를 가리키는 이름이고, 쿠버네티스가 ALB Ingress Controller보다 먼저 생긴 규칙이라 그 이름을 그대로 재사용하고 있다는 걸 설명했다.
- **혼동 — 라우팅 테이블이 몇 개 필요한지**: 사용자가 이미 만든 `public-rt`(1개)와 지금 안내한 `private-a-rt`/`private-c-rt`(2개)를 같은 걸로 착각해서 "아까 만들지 않았나?"라는 질문이 나왔다 — 총 3개(퍼블릭 1 + 프라이빗 2)가 필요하다는 걸 다시 정리해서 풀었다.
- **질문 — NAT "리전별-신규" 옵션을 써도 되나?**: 위 "문서에 없던 UI 변화" 항목과 동일한 이유로 안 된다고 답하고 "가용 영역" 방식으로 유도했다.

### 2단계 — IAM 사용자
새로 안 만들고 기존 `jhlee`(`AdministratorAccess`)를 그대로 썼다. `aws sts get-caller-identity`로 계정 ID(`428270342381`)를 확인했고, 이후 모든 단계에서 이 값을 하드코딩된 `815373273907` 자리에 대신 넣었다.

### 3단계 — IAM 역할
문서 그대로 `eksClusterRole`(`AmazonEKSClusterPolicy`), `eksNodeRole`(`AmazonEKSWorkerNodePolicy`+`AmazonEKS_CNI_Policy`+`AmazonEC2ContainerRegistryReadOnly`) 둘 다 새로 만들었다. 이슈 없음.

### 4단계 — EKS 클러스터 + 노드 그룹
- **질문 — 클러스터 생성 화면에서 왜 서브넷 6개를 다 선택하나, 워커 노드는 프라이빗 컴퓨팅에만 뜰 텐데?**: 클러스터 생성 화면의 "Subnets"는 컨트롤 플레인이 ENI를 낼 수 있는 서브넷 풀을 등록하는 것이고, 실제 워커 노드 배치는 그다음 "노드 그룹 추가" 단계에서 별도로 결정한다는 걸 설명했다(그래서 노드 그룹에서는 `private-compute-a`/`c`만 선택). 클러스터 등록 서브넷을 넉넉히(6개 다) 잡아두는 이유는 ALB 컨트롤러가 나중에 퍼블릭 서브넷을 찾을 때, 그리고 VPC/서브넷을 EKS 생성 후엔 바꾸기 어렵기 때문이라고 정리했다.
- **인스턴스 타입 결정**: 새 계정이라 프리티어 제한이 없어서 아무 타입이나 고를 수 있게 됐다. Claude는 `xray-ai`의 CPU 기반 YOLO 추론(결함 탐지)이 병목이 될 걸 감안해 `m6i.large`~`m6i.xlarge`를 추천했지만(GPU는 코드가 `XRAY_DEVICE=cpu`로 고정돼 있어 낭비라고 설명), 최종적으로는 **`t3.medium`**(2 vCPU/4GB)으로 진행하기로 결정.
- **결과**: `t3.medium` 노드 2대가 정상적으로 `Ready` 상태가 됐고, 내부 IP가 각각 `10.0.10.124`, `10.0.11.49`로 `private-compute-a`/`c` 서브넷 대역과 정확히 일치함을 `kubectl get nodes -o wide`로 확인했다.

### 5단계 — kubectl 로컬 연동 (Access Entry)
실제로는 **Access Entry를 따로 만들 필요가 없었다** — 콘솔에서 클러스터를 만든 신원과 CLI로 `aws eks update-kubeconfig`를 실행한 신원이 둘 다 `jhlee`로 동일해서, `kubectl get nodes`가 추가 설정 없이 바로 정상 동작했다. (문서가 우려하는 "만든 신원 ≠ CLI 신원" 문제 자체가 이번엔 발생하지 않은 케이스.)

### 6단계 — OIDC 자격 증명 공급자 연동
문서 그대로 진행, 특이사항 없음.

### 7단계 — AWS Load Balancer Controller 설치
IAM 정책/IRSA 역할 생성까지는 문제없었고, 로컬 환경(Windows, PowerShell) 특유의 에러 2건이 있었다.

- **에러 1 — `helm`이 인식 안 됨**: `helm repo add ...` 실행 시 `'helm' 용어가 cmdlet, 함수, 스크립트 파일 또는 실행할 수 있는 프로그램 이름으로 인식되지 않습니다`. 로컬에 Helm이 설치돼 있지 않았던 것 — `winget install Helm.Helm`으로 설치하고 터미널을 새로 열어 PATH를 반영해서 해결했다.
- **에러 2 — PowerShell 줄바꿈 문법 오류**: `helm install ...` 명령을 여러 줄로 나눠 쓸 때 bash 스타일로 줄 끝에 `|`(파이프)를 넣었다가(bash의 `\`를 잘못 옮겨 씀) `단항 연산자 '--' 뒤에 식이 없습니다` 같은 파서 에러가 여러 개 발생했다. PowerShell은 줄 이어쓰기가 백틱(`` ` ``)이고 `|`는 파이프 연산자로 해석된다는 걸 설명하고, 한 줄짜리 명령으로 합쳐서 재시도해 해결했다.
- **결과**: Helm 설치 성공(`STATUS: deployed`), `kubectl get deployment -n kube-system aws-load-balancer-controller`에서 `AVAILABLE 2/2` 확인(Helm 차트 기본 레플리카 수가 2라 정상, 최소 1 이상이면 됨).

### 8단계 — RDS(PostgreSQL) 생성
문서는 기본적으로 Multi-AZ를 전제로 쓰여 있지만, 이번 배포에서는 **비용 때문에 Single-AZ로 최종 결정**했다(전체 배포 완료 후 필요하면 콘솔에서 무중단으로 Multi-AZ 전환 예정 — RDS는 이 전환에 엔드포인트 변경이 없어 애플리케이션 쪽 설정은 그대로 둬도 된다).

- **질문 — "가용성 및 내구성" 화면의 3개 옵션 중 뭘 골라야 하나?**(스크린샷 첨부): "다중 AZ DB 클러스터(인스턴스 3개, 읽기 가능한 대기 2개 — 읽기 확장용, 이번엔 과함)", "다중 AZ DB 인스턴스(인스턴스 2개, 읽기 불가능한 대기 1개)", "단일 AZ(인스턴스 1개)" 세 가지 차이를 설명하고, 문서가 말하는 Multi-AZ는 가운데 옵션과 정확히 일치한다고(이미 콘솔에서 기본 선택돼 있었음) 확인해줬다.
- **질문 — AZ-a에 RDS 하나만 있어도 AZ-c의 EC2가 접속되는데, 그럼 Multi-AZ가 왜 필요한가?**: VPC 안에서는 서브넷이 어느 AZ에 있든 기본적으로 항상 서로 통신 가능(자동 local 라우트)하다는 것과, Multi-AZ의 목적은 "네트워크 도달성"이 아니라 "AZ 전체가 죽었을 때 다른 AZ의 대기 인스턴스로 자동 failover"하기 위한 것이라는 차이를 설명했다.
- **문서와 달랐던 점 — DB 서브넷 그룹 드롭다운이 비어있음**: DB 생성 화면의 "DB 서브넷 그룹" 드롭다운에 "새 DB 서브넷 그룹 생성"만 있고 기존 그룹을 고를 수 없었다 — 신규 계정이라 서브넷 그룹이 하나도 없어서 당연한 현상이었다. RDS 콘솔의 별도 **"서브넷 그룹"** 메뉴에서 `conservation-db-subnet-group`을 먼저 만들고(VPC `conservation-vpc`, AZ `2a`/`2c`, 서브넷은 `private-data-a`/`private-data-c`만 체크) 다시 DB 생성 화면으로 돌아와 선택하는 방식으로 풀었다.
- **질문 — 보안 그룹 인바운드 규칙이 정확히 무슨 뜻인가**(`conservation-db-sg`에 `PostgreSQL`, 소스=워커 노드 SG): "RDS로 들어오는 트래픽 중 5432 포트, 출발지가 그 보안 그룹이 붙은 리소스인 경우만 허용"이라는 뜻이 맞고, IP 대역이 아니라 보안 그룹을 참조하는 방식이라 워커 노드가 재시작/오토스케일링돼도 규칙을 다시 안 고쳐도 된다는 점, 이 규칙이 없으면 2차 배포 때 실제로 겪었던 `conservation-guide-ai` 무한 재시작(`PoolTimeout`) 문제가 재현된다는 점을 설명했다.

### 9단계 — ECR
계정 ID `428270342381` 기준으로 4개 리포지토리(`conservation-backend`/`conservation-guide-ai`/`xray-ai`/`pottery-inspection-ai`)를 만들고 로컬 build/tag/push 전부 성공. (문서 개정에 따라 Lambda용 5번째 리포지토리는 애초에 만들지 않음 — 14단계에서 zip 배포로 처리 예정.)

### 10단계 — pottery-inspection-ai 준비
별도 확인 작업 없이, 9단계에서 이 이미지의 빌드/푸시가 에러 없이 끝났다는 사실 자체로 `ai_hub/pottery_multitask_model_v2` COPY가 정상 동작했다고 판단하고 통과 처리했다.

### 11단계 — Secret 생성
- **질문 — IAM 액세스 키 발급 화면의 "액세스 키 모범 사례 및 대안"에서 뭘 선택해야 하나?**: 실제 용도(EKS Pod에서 쓰는 정적 키)로는 "애플리케이션이 AWS 컴퓨팅 서비스에서 실행 중"이 정확하지만 AWS가 강하게 IAM 역할(IRSA) 사용을 권고하는 경고만 뜨고 결과는 동일하므로, 경고를 덜 보고 싶으면 **"기타(Other)"**를 선택해도 무방하다고 안내했다. (IRSA 전환은 18단계 "남은 작업" 2번에 이미 적혀 있는 미해결 항목.)
- **질문 — OpenSSL 설치 방법**: Git Bash에 이미 포함돼 있을 가능성이 높아 `openssl version`으로 먼저 확인해보라고 안내했고, 없을 경우 `winget install ShiningLight.OpenSSL.Light`, 또는 아예 설치 없이 PowerShell 네이티브(`Get-Random` 조합)로 `JWT_SECRET`/`XRAY_STITCH_CALLBACK_TOKEN`을 생성하는 대안도 제공했다.
- **순서 조정 — S3 버킷을 11단계 중간에 앞당겨 생성**: `.env.k8s`의 `AWS_S3_BUCKET` 값을 채우려면 버킷이 먼저 있어야 해서, 원래 14-1(S3+Lambda 단계)에 있는 버킷 생성만 떼어서 지금 미리 했다. (문서에도 "버킷 생성은 Lambda 배선과 무관하게 먼저 해도 된다"고 이미 적어뒀던 부분이라 문서와 어긋나지 않음.)
- **버킷 이름 변경**: 처음엔 `conservation-xray-uploads-428270342381`을 예시로 제안했는데, 사용자가 "이 버킷은 X-ray 전용이 아니라 습윤 효과 테스트 사진 등 다른 이미지 업로드(`S3PhotoStorageService`)에도 같이 쓰인다"는 이유로 더 일반적인 이름을 원했다. S3 버킷 이름이 AWS 전체에서 유일해야 한다는 제약을 설명하고, 계정 ID를 붙인 **`conservation-image-uploads-428270342381`**로 최종 결정했다.
- **결과**: `kubectl create secret generic app-secrets --from-env-file=.env.k8s` 실행 후 `kubectl get secret`에서 `DATA 12` 확인 — `.env.k8s`에 채운 12개 키(`OPENAI_API_KEY`/`POSTGRES_PASSWORD`/`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_S3_BUCKET`/`AWS_REGION`/`SPRING_DATASOURCE_URL`/`SPRING_DATASOURCE_USERNAME`/`DATABASE_URL`/`JWT_SECRET`/`JWT_EXPIRATION_MS`/`XRAY_STITCH_CALLBACK_TOKEN`)와 정확히 일치.

### 12단계 — Deployment/Service/Ingress 매니페스트
레포의 `k8s/app.yaml`이 (지난번 문서 개정 때 예상한 대로) 옛날 버전(EFS 볼륨 + 계정 `815373273907`) 그대로 남아있는 걸 확인하고, Claude가 문서 12단계에 적힌 새 내용을 실제 파일에 반영했다(직접 파일 편집 — 사용자가 콘솔/kubectl만 실행). 자세한 변경 내용은 바로 아래 "생성된 `k8s/app.yaml` 해설" 참고.

### 13단계 — 적용 및 검증
`kubectl apply -f k8s/app.yaml` 실행 결과 Deployment 4개 + Service 4개 + Ingress 1개, 총 9개 리소스가 전부 정상 생성됐다.

- **경고(무해)**: `Warning: annotation "kubernetes.io/ingress.class" is deprecated, please use 'spec.ingressClassName' instead`. 리소스 생성 자체는 성공했고 AWS Load Balancer Controller가 옛날 방식 annotation도 여전히 인식해서 동작에 지장이 없다 — 나중에 `spec.ingressClassName: alb`로 바꾸는 게 최신 권장 방식이라는 정도로만 기록.
- **결과**: `kubectl get pods -w`에서 Deployment 4개 전부 `1/1 Running`, 재시작 0회로 확인. `kubectl get ingress conservation-backend`의 `ADDRESS`에도 ALB 도메인(`k8s-default-conserva-a3c5743a4e-1915528239.ap-northeast-2.elb.amazonaws.com`)이 정상적으로 채워졌다.

### 14단계 — S3 + Lambda(`xray-s3-file-recorder`)

S3 버킷(`conservation-image-uploads`)은 11단계에서 이미 만들어둔 상태라 14-2부터 진행했다. Lambda 함수 이름은 문서 예시(`xray-s3-file-recorder`)와 다르게 **`conservation-xray-s3-file-recorder`**로 직접 지었다 — 함수 이름은 코드/IAM 어디에서도 문자열로 참조되지 않고 ARN으로만 연결되기 때문에 이름을 바꿔도 기능상 문제는 없다(다만 보안 그룹·실행 역할과 이름이 안 맞아 나중에 헷갈릴 수 있다는 점만 안내).

**질문 — VPC 엔드포인트가 뭐고, 서비스 이름은 뭐고, Gateway/Interface 중 왜 Gateway인가?**: VPC 엔드포인트는 프라이빗 서브넷에서 NAT/인터넷을 거치지 않고 AWS 서비스로 바로 가는 사설 지름길이라는 것, 서비스 이름(`com.amazonaws.<리전>.<서비스>`)은 그 지름길이 정확히 어느 서비스로 가는지 지정하는 값이라는 것, Gateway 타입(S3/DynamoDB 전용, 실제로는 라우팅 테이블에 규칙 하나 추가하는 방식, 무료)과 Interface 타입(서브넷에 실제 ENI를 만드는 PrivateLink 방식, 시간당 과금)의 차이를 설명하고, S3는 Gateway로 충분하니 그걸 쓴다고 안내했다. 이어서 "라우팅 테이블을 지정하는 게 Gateway 규칙을 어디에 추가할지 정하는 것 맞냐"는 확인 질문에도 맞다고 답하고, `private-a-rt`/`private-c-rt`를 고른 이유(그 두 테이블이 `xray-ai`/Lambda가 실제로 걸려있는 서브넷들의 라우팅 테이블이라서)를 짚어줬다.

**질문 — 보안 그룹 이름이 왜 `xray-s3-file-recorder-sg`인가?**: 이 SG가 속한 Lambda 함수의 실제 이름을 그대로 따서 붙인 것이고, 원래 문서 초안에서 가상의 이름(`xray-upload-notifier`)이었다가 실제 코드베이스의 진짜 구현체 이름(`xray-s3-file-recorder`)으로 문서를 개정하면서 함수/역할 이름은 바꿨는데 SG 이름만 옛날 이름(`lambda-notifier-sg`)이 남아있던 불일치를, 문서 검증 때 발견해서 통일했다는 배경을 설명했다.

**진행 중 부딪힌 UX 이슈 — IAM 역할 생성 마법사에서 관리형 정책과 인라인 정책을 동시에 못 붙임**: IAM "역할 생성" 화면은 기존(관리형/고객관리형) 정책 연결만 되고 인라인 정책 생성 옵션이 없다. 그래서 (1) `AWSLambdaVPCAccessExecutionRole`만 붙여서 역할을 먼저 만들고, (2) 생성된 역할 상세 페이지의 "권한 추가 → 인라인 정책 생성"에서 S3 `GetObject` JSON을 추가하는 2단계로 나눠 진행했다.

**에러 1 — `build_zip.sh` 실행 안 됨(더블클릭/PowerShell)**: 처음엔 Windows 탐색기에서 `.sh` 파일을 실행하려다 "열 앱 선택" 창이 떴다고 해서 "Git Bash 안에서 실행해야 한다"고 안내했는데, 실제로는 이미 콘솔(PowerShell)에서 `./build_zip.sh`로 실행한 상태였다. 진짜 원인은 PowerShell이 `.sh`를 해석 못 해서 OS에 "이 파일 뭘로 열지" 위임한 것 — Git Bash(또는 WSL)에서 실행해야 한다는 점을 정정해서 설명했다. 최종적으로는 Claude가 Bash 도구로 직접 `./build_zip.sh`를 실행해 `dist/xray-s3-file-recorder.zip`을 생성했다.

**에러 2 — S3 HeadObject 403 Forbidden**: 트리거 연결 후 테스트 업로드(`aws s3 cp`)를 했더니 첫 시도는 `ignored: 1`(키 패턴이 안 맞아 무시, 정상), 두 번째 시도는 `errors: ["...403 Forbidden..."]`이 났다. 원인은 사용자가 Lambda 인라인 정책의 `Resource` ARN을 `conservation-image-uploads-428270342381/xray/*`(계정 ID 접미사 포함)로 넣었는데, `aws s3 ls`로 직접 확인해보니 실제 버킷 이름은 접미사 없는 **`conservation-image-uploads`**였다 — 즉 존재하지 않는 버킷을 가리키는 정책이라 403이 난 것. 사용자가 직접 인라인 정책 JSON을 실제 버킷 이름으로 수정해서 해결했다. 이 김에 `app-secrets`의 `AWS_S3_BUCKET` 값도 `kubectl get secret ... | base64 -d`로 확인했는데, 이쪽은 처음부터 접미사 없이 정확하게 들어가 있어서 추가 조치는 필요 없었다.

**에러 3 — RDS `NotNullViolation`(`created_at`)**: 403이 풀리고 나니 이번엔 `s3_file` INSERT가 `created_at` 컬럼 NOT NULL 위반으로 실패했다. 원인은 위 8단계 절에 추가한 "실전에서 발견된 함정" 내용과 동일 — `S3FileRecord.java` JPA 엔티티가 `created_at`/`updated_at`에 DB 레벨 기본값을 안 걸어놔서 `ddl-auto=update`가 만든 실제 테이블엔 `DEFAULT CURRENT_TIMESTAMP`가 없는데, Lambda 쪽 코드(`repository.py`)는 그 기본값의 존재를 전제로 짜여 있었던 것. psql Pod에서 `ALTER TABLE ... SET DEFAULT CURRENT_TIMESTAMP`를 두 컬럼에 적용해서 해결했다.

**에러(?) 4 — CloudWatch Logs 페이지에 로그가 안 보임**: Lambda 함수 페이지 안의 "최근 호출" 목록(모니터링 탭)에는 정상적으로 항목이 쌓이는데, 별도의 CloudWatch Logs 콘솔 페이지로 이동하면 로그가 없어 보인다는 질문이 나왔다. 실제로는 에러가 아니라 (1) 계정에 이름이 비슷한 로그 그룹이 두 개(`/aws/lambda/conservation-xray-s3-file-recorder`와, 함수 이름을 바꾸기 전 흔적으로 보이는 `/aws/lambda/xray-s3-file-recorder`) 있어서 헷갈렸을 가능성, (2) Lambda가 콜드 스타트마다 새 로그 스트림을 만드는데 오래된 스트림을 보고 있었을 가능성 두 가지를 짚어줬고, 사용자가 최신 로그 스트림을 다시 찾아서 확인하니 `{"processed": 1, "ignored": 0, "errors": []}`로 정상 처리를 확인했다.

**결과**: 14단계 전체 완료. RDS `s3_file` 테이블에 테스트 업로드 레코드가 정상 적재됨.

### 기능 테스트 안내 + FE 연동 트러블슈팅

14단계까지 인프라(Pod/ALB/Lambda)를 검증한 뒤, 실제 애플리케이션 기능을 테스트하는 방법을 정리해서 안내했다. 컨트롤러 코드를 직접 확인해 실제 존재하는 엔드포인트 기준으로 정리한 결과:

| 기능 | 엔드포인트 | 인증 | 비고 |
|---|---|---|---|
| 헬스체크 | `GET /api/xray/health` | 불필요 | |
| 회원가입/로그인/로그아웃 | `POST /api/users/signup`, `/login`, `/logout` | 불필요 | 로그인 응답의 JWT를 이후 요청에 사용 |
| 게시판 조회 | `GET /api/posts/**` | 불필요 | |
| 게시판 쓰기/수정/삭제 | `POST`/`PUT`/`DELETE /api/posts/**` | **필요**(JWT) | `WebSecurityConfig`에서 `authenticated()` |
| 도자기 검수 | `POST /pottery-inspection`(multipart) | 불필요 | 단발성 API, curl 하나로 테스트 가능 |
| 보존처리 가이드 | `POST /tasks/{taskId}/start`, `/resume`, `GET /tasks/{taskId}` | 불필요 | LangGraph 상태머신 — 여러 번의 `/resume`을 거치는 대화형 플로우라 curl 몇 줄로는 안 끝남, 노드별 스키마는 레포 `README.md` 참고 |
| X-ray 결합 | `POST /api/xray/stitch/jobs/prepare`, `/start` 등 | 불필요 | 레포에 이미 있는 `tools/test_xray_s3_flow.py`로 prepare→S3 업로드→start→콜백 대기까지 자동 테스트 가능 |
| X-ray 결함 탐지 | `POST /api/xray/jobs/{jobId}/detect`, `GET .../defects`, `POST .../report-text/generate`, `POST .../complete` | 불필요 | 결합이 끝난 `jobId` 필요 |

**FE 연동 트러블슈팅 — S3 CORS 에러**: FE(`http://localhost:5174`)가 presigned URL로 S3에 직접 PUT하려다 브라우저 콘솔에 `Access to fetch at '...s3.amazonaws.com/...' has been blocked by CORS policy: ... No 'Access-Control-Allow-Origin' header` 에러가 났다. 원인은 `WebConfig.java`의 `app.cors.allowed-origins`(Spring 자체 CORS)와 **S3 버킷 CORS는 완전히 별개**라는 걸 놓친 것 — FE가 Spring을 안 거치고 브라우저에서 S3로 직접 요청을 보내니, S3 버킷에도 따로 CORS 설정이 있어야 한다. S3 콘솔 → 버킷 → 권한 탭 → CORS 섹션에 `AllowedOrigins`(`localhost:5173`/`5174`/`3000`), `AllowedMethods`(`GET`/`PUT`/`HEAD`), `AllowedHeaders: ["*"]`(presigned URL의 `X-Amz-SignedHeaders`에 `x-amz-meta-usage` 같은 커스텀 헤더가 포함돼 있어서 와일드카드로)를 추가해서 해결했다. 이 내용은 앞으로 이 실수를 반복하지 않도록 위 **14-1단계 본문에도 영구적으로 추가**했다.

---

## 부록 B — 생성된 `k8s/app.yaml` 해설

이 기록 시점에 실제로 적용한 `k8s/app.yaml`은 **Deployment 4개 + Service 4개 + Ingress 1개**, 총 9개의 쿠버네티스 리소스를 하나의 파일에 `---`로 이어붙인 구성이다.

### 공통 패턴
네 개의 Deployment(`conservation-guide-ai`, `xray-ai`, `pottery-inspection-ai`, `conservation-backend`) 모두 같은 뼈대를 쓴다:
- `replicas: 1` — 지금은 각 서비스 인스턴스가 1개씩만 뜬다(트래픽이 늘면 늘릴 수 있는 자리).
- `image: 428270342381.dkr.ecr.ap-northeast-2.amazonaws.com/<서비스명>:latest` — 9단계에서 push한 ECR 이미지를 그대로 pull.
- `envFrom: secretRef app-secrets` — 11단계에서 만든 Secret의 키 12개가 **전부 통째로** 컨테이너 환경변수로 주입된다. 그래서 각 Deployment의 `env` 블록에는 서비스별로 필요한 값만 개별로 추가돼 있고, `AWS_S3_BUCKET`이나 `JWT_SECRET`처럼 공통/민감한 값은 `env`에 안 적혀 있어도 이미 들어가 있다.
- 이어서 같은 이름의 `Service`(`type: ClusterIP`)가 붙는다 — 클러스터 내부에서만 접근 가능한 가상 IP로, 서비스 이름(`http://xray-ai:8000` 같은 식)이 곧 DNS 주소가 된다.

### `conservation-guide-ai`
가장 단순하다. 포트 8000, `envFrom`만 있고 서비스 고유의 `env`는 없다 — `OPENAI_API_KEY`, `DATABASE_URL` 등 필요한 값이 전부 Secret에서 오기 때문.

### `xray-ai`
`env`에 X-ray 결합 엔진 설정이 여러 개 있다:
- `XRAY_DEVICE`/`XRAY_DEFAULT_CONF`/`XRAY_NMS_IOU`: 결함 탐지 모델(YOLO) 추론 설정.
- `XRAY_STITCH_*`: 결합 엔진 스크립트/설정 파일 경로. 이 중 `XRAY_STITCH_JOBS_ROOT`와 `XRAY_STITCH_CACHE_DIR`가 `/tmp/xray_jobs`, `/tmp/xray_cache`로 돼 있는 게 EFS 시절과 다른 지점이다 — 이제 이 경로는 **컨테이너가 재시작되면 사라져도 되는 임시 작업공간**이고, 실제 산출물(조각 결합 이미지, 레이아웃 JSON 등)은 S3에 올라가는 게 정본이라 볼륨 마운트가 필요 없다.
- 그래서 이 Deployment에는 `volumeMounts`/`volumes`가 **아예 없다** — 2차/이전 문서에 있던 `shared-storage`(EFS PVC) 마운트가 통째로 사라진 부분.

### `pottery-inspection-ai`
`hf-cache`라는 `emptyDir` 볼륨을 `/root/.cache/huggingface`에 마운트한다. 이건 EFS가 아니라 **Pod 로컬 임시 스토리지**로, Hugging Face 모델을 매번 새로 안 받고 같은 Pod가 살아있는 동안은 캐시를 재사용하려는 용도다(Pod가 재시작되면 캐시도 같이 날아가지만, 시대 판정 모델 자체는 이미지 안에 COPY돼 있어서 기능에는 지장 없음).

### `conservation-backend`
가장 설정이 많다:
- `CONSERVATION_GUIDE_AI_BASE_URL`/`POTTERY_INSPECTION_AI_BASE_URL`/`XRAY_AI_BASE_URL`: 다른 3개 서비스의 k8s 내부 DNS 주소. Pod가 아니라 **Service** 이름을 가리키는 게 핵심이다 — 실제 Pod IP는 재시작될 때마다 바뀌지만 Service의 ClusterIP/DNS 이름은 고정이라, 이 주소들은 바뀔 일이 없다.
- `XRAY_STITCH_CALLBACK_URL`: `http://conservation-backend:8080/api/xray/stitch/callback` — 이 값이 좀 독특한데, **자기 자신을 가리키는 주소**다. `xray-ai`(FastAPI)가 결합을 끝내면 이 URL로 다시 `conservation-backend`를 호출해서 "끝났다"고 알려주는 콜백 경로라, Spring이 자기 Service 이름으로 스스로를 가리키는 게 맞는 구성이다.
- `SPRING_DATASOURCE_PASSWORD`는 `env`에서 `secretKeyRef`로 **개별 지정**돼 있다 — 나머지는 `envFrom`으로 뭉텅이로 들어오는데 이 값만 따로 뽑아 쓰는 이유는, Spring Boot의 `spring.datasource.password` 플레이스홀더가 특정 환경변수 이름(`SPRING_DATASOURCE_PASSWORD`)을 정확히 요구하기 때문 — Secret의 실제 키 이름(`POSTGRES_PASSWORD`)과 Spring이 찾는 환경변수 이름이 달라서, `key: POSTGRES_PASSWORD`로 값을 가져와 `SPRING_DATASOURCE_PASSWORD`라는 이름으로 다시 매핑해주는 것.
- 이 Deployment에도 `volumeMounts`/`volumes`가 없다 — 파일 뒤에 남긴 주석(`# shared-storage(EFS) 볼륨 없음 — X-ray 산출물은 S3에 저장되고 이 Pod의 로컬 디스크는 쓰지 않는다.`)이 그 이유를 짚어둔 것.
- `Service`만큼은 다른 셋과 다르게 **`type: LoadBalancer`가 아니라 `ClusterIP`**다 — 외부 노출은 이 Service가 아니라 바로 아래 `Ingress`가 맡는 구조이기 때문(2차 배포는 Service를 `LoadBalancer`로 바꿔서 NLB류를 자동 생성했지만, 3차는 진짜 ALB를 Ingress로 만든다).

### `Ingress`
파일 맨 끝의 유일한 `Ingress` 리소스가 실질적인 "외부 진입점" 정의다:
- `kubernetes.io/ingress.class: alb`: 이 Ingress를 7단계에서 설치한 AWS Load Balancer Controller가 처리하라는 지정.
- `alb.ingress.kubernetes.io/scheme: internet-facing`: ALB 노드를 퍼블릭 서브넷(`public-a`/`c`)에 만들라는 뜻 — 1단계에서 붙인 `kubernetes.io/role/elb` 태그 덕분에 컨트롤러가 어느 서브넷인지 자동으로 찾는다.
- `alb.ingress.kubernetes.io/target-type: ip`: ALB가 워커 노드의 NodePort를 거치지 않고 **Pod IP로 직접** 트래픽을 쏜다는 뜻 — 홉이 하나 줄어 2차의 `LoadBalancer` 방식보다 지연이 약간 적다.
- `rules`: 경로 `/`(전부)를 `conservation-backend` Service의 8080 포트로 전달. 즉 X-ray/보존처리 가이드/도자기 검수 등 모든 API가 결국 `conservation-backend` Pod 하나(정확히는 그 Service)를 거쳐 들어온다 — 나머지 3개 AI 서비스는 외부에 직접 노출되지 않고 `conservation-backend`를 통해서만 내부 호출된다.
