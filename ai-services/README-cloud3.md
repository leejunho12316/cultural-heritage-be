# AWS ECR + EKS 배포 가이드 (3차 — 최종 아키텍처, `BigProject09_AWS.drawio.png` 기준)

이 문서는 `ai-services/BigProject09_AWS.drawio.png`에 확정된 아키텍처를 그대로 구현하는 절차다. 2차 배포(`README-cloud2.md`)와 비교하면 다음 4가지가 새로 생기거나 크게 바뀐다.

| 구분 | 2차 배포 | 3차 배포(이 문서) |
|---|---|---|
| VPC | 기본(default) VPC 그대로 사용 (전부 퍼블릭 서브넷) | **커스텀 VPC** — AZ 2개 × (퍼블릭/프라이빗-컴퓨팅/프라이빗-데이터) 서브넷 6개, NAT Gateway 2개 |
| RDS | 단일 AZ (`db.t3.micro` 프리티어) | **Multi-AZ**(대기 인스턴스 자동 복제) — 프리티어 범위 밖, 비용 증가 |
| 외부 노출 | `conservation-backend` Service를 `type: LoadBalancer`로 변경(NLB류 자동 생성) | **ALB Ingress** — AWS Load Balancer Controller를 설치해 진짜 ALB를 씀 (2차의 "남은 작업" 1번을 여기서 해결) |
| 배포 자동화 | 로컬에서 수동 `docker build/push` + 수동 `kubectl apply` | **CI/CD** — CodeCommit → CodePipeline → CodeBuild → ECR (2차의 "남은 작업" 3번을 여기서 해결) |
| 신규 기능 | 없음 | **S3 + Lambda** — X-ray 이미지 업로드 신호를 Lambda가 받아 RDS에 기록 |

이 문서는 2차와 마찬가지로 AWS 계정에 **아무것도 없는 상태**(또는 2차 자원과 별도로 새 VPC에 새로 구축하는 상태)를 가정한다. 계정ID `815373273907` / 리전 `ap-northeast-2`(서울) 기준으로 쓰고, AZ는 `ap-northeast-2a`(이하 AZ-A) / `ap-northeast-2c`(이하 AZ-C) 2개를 쓴다. 새로 만들 때마다 값이 달라지는 것(서브넷 ID, RDS 엔드포인트 등)은 `<...>`로 표기했다.

> **먼저 결정할 것**: 2차에서 만든 EKS 클러스터/RDS/EFS가 기본 VPC 위에 그대로 남아있다면, 이번 아키텍처는 **커스텀 VPC가 필수**라서 기존 클러스터를 그 안으로 옮길 수 없다(VPC는 나중에 못 바꾸는 리소스). 즉 이번 작업은 "업그레이드"가 아니라 **새 VPC에 새로 짓는 것**이다. 2차 자원을 계속 쓸 계획이 없다면, 비용이 겹치지 않도록 미리 정리(EKS 노드 그룹 축소/삭제, RDS 삭제 등)해두는 걸 권장한다.

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
| RDS(PostgreSQL, Multi-AZ) | 앱 DB + LangGraph 체크포인터 | 프라이빗 데이터 서브넷 (Primary/Standby 각 AZ) |
| EFS | `conservation-backend` ↔ `xray-ai` 공유 스토리지(`/shared`) | 프라이빗 컴퓨팅 서브넷과 동일 AZ에 마운트 타깃 |
| S3 | X-ray 원본 이미지 업로드 대상 | AWS 리전 서비스 (VPC 밖) |
| S3 Gateway Endpoint | Lambda가 NAT 없이 S3 API 호출 | 프라이빗 컴퓨팅 서브넷 라우팅 테이블에 연결 |
| Lambda | S3 업로드 이벤트 트리거 → RDS에 상태 기록 | 프라이빗 컴퓨팅 서브넷 (VPC 연결) |
| CodeCommit/CodePipeline/CodeBuild | CI/CD | VPC 밖(관리형) |
| ECR | 컨테이너 이미지 저장소 (5개: 앱 4개 + Lambda 1개) | VPC 밖(관리형) |

### 전체 흐름
```
사용자 → Route53 → IGW → ALB → EC2(워커노드의 conservation-backend Pod)
                                     ↓ (내부 호출)
                    conservation-guide-ai / xray-ai / pottery-inspection-ai
                                     ↓
                                   RDS(Primary, Multi-AZ)

EC2(xray-ai 등) → S3 (이미지 업로드)
S3 → Lambda (이벤트 트리거)
Lambda → S3 Endpoint → S3 (필요 시 객체 조회)
Lambda → RDS(Primary) (업로드 상태 기록)

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
| `xray-upload-notifier-role` | Lambda 실행 역할 | 17단계 |

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

이번엔 이 OIDC 공급자를 EFS CSI 드라이버(9단계)뿐 아니라 **AWS Load Balancer Controller(7단계)**, 필요하면 **Lambda 실행 역할(17단계, VPC 관련 정책만 있으면 되므로 실제로는 OIDC 불필요)** 에서도 재사용한다.

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
4. 생성 후 **신뢰 관계** 탭에서 `Condition`을 서비스 계정 단위로 제한(EFS 때와 같은 패턴):
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
`AVAILABLE`이 1 이상이면 완료. (Ingress 리소스 자체는 15단계에서 만든다 — 지금은 컨트롤러만 설치된 상태.)

---

## 8단계 — EFS 생성

2차 5단계와 개념은 동일하고, 마운트 타깃이 이번엔 **프라이빗 컴퓨팅 서브넷**(`private-compute-a`, `private-compute-c`)에 생긴다는 점만 다르다.

EFS 콘솔 → 파일 시스템 생성: 이름 `conservation-shared` / VPC `conservation-vpc` / Regional(멀티 AZ)

### 보안 그룹 — NFS(2049) 허용
1. EFS 콘솔 → 네트워크 탭 → 보안 그룹 ID 확인
2. EC2 콘솔에서 그 보안 그룹 → 인바운드 규칙 추가: 유형 `NFS` / 소스 = **워커 노드 보안 그룹**

> **주의(2차에서 실제로 겪음, 여기서도 동일하게 적용됨)**: 소스 입력창에 보안그룹 ID를 직접 타이핑/붙여넣기 하면 "기존 IPv4 CIDR 규칙에 참조된 그룹 ID을(를) 지정할 수 없습니다" 에러가 난다. 반드시 입력창을 클릭해서 뜨는 자동완성 드롭다운에서 선택해야 한다. 이 아래 RDS(11단계), Lambda(17단계) 보안 그룹 설정에도 전부 동일하게 적용된다.

---

## 9단계 — EFS CSI 드라이버용 IAM 역할 + 애드온 설치

2차 7단계와 완전히 동일 — OIDC 기반 `AmazonEKS_EFS_CSI_DriverRole`(`AmazonEFSCSIDriverPolicy`)을 만들고, 신뢰 정책에 `system:serviceaccount:kube-system:efs-csi-controller-sa` 조건을 걸고, EKS Pod Identity 신뢰 문장도 추가한 뒤, 애드온(Amazon EFS CSI Driver)을 설치한다. 자세한 JSON은 2차 문서 7단계 참고.

```bash
kubectl get pods -n kube-system | grep efs-csi
```

---

## 10단계 — EFS StorageClass / PersistentVolume / PersistentVolumeClaim

2차 10단계와 완전히 동일한 `k8s/storage.yaml`을 그대로 쓴다. `volumeHandle`만 이번에 새로 만든 EFS 파일시스템 ID로 바꾼다.

```bash
kubectl apply -f k8s/storage.yaml
kubectl get pv,pvc
```

---

## 11단계 — RDS(PostgreSQL, Multi-AZ) 생성

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
DB 상세 → 연결 및 보안 → 보안 그룹 → 인바운드 규칙 추가: 유형 `PostgreSQL` / 소스 = 워커 노드 보안 그룹 (+ 17단계에서 만들 **Lambda 보안 그룹**도 여기 추가해야 함, 지금은 아직 없으니 17단계에서 다시 돌아와서 추가)

### `conservation` 데이터베이스 존재 확인
2차 8단계와 동일한 방법(임시 psql Pod)으로 확인 — Multi-AZ여도 접속 엔드포인트는 Primary를 가리키는 단일 엔드포인트라 절차는 동일하다.
```bash
kubectl run psql-tmp --rm -it --image=postgres:16 --restart=Never -- \
  psql "postgresql://conservation:<마스터 암호>@<엔드포인트>:5432/postgres"
```
`\l`로 `conservation` 있는지 확인, 없으면 `CREATE DATABASE conservation;`.

---

## 12단계 — ECR 리포지토리 생성 + 이미지 최초 push

2차 2단계와 동일하되, 이번엔 리포지토리가 **5개**다(앱 4개 + Lambda 1개).

| Repository name | Dockerfile 위치 |
|---|---|
| `conservation-backend` | 레포 루트 `/Dockerfile` |
| `conservation-guide-ai` | `/ai-services/conservation-guide-ai/Dockerfile` |
| `xray-ai` | `/ai-services/xray-ai/Dockerfile` |
| `pottery-inspection-ai` | `/ai-services/pottery-inspection-ai/Dockerfile` |
| `xray-upload-notifier` | `/lambda/xray-upload-notifier/Dockerfile` (17단계에서 작성) |

CI/CD(18단계)가 갖춰지기 전까지, 최초 1회는 2차와 동일하게 로컬에서 수동으로 build/tag/push 해서 EKS가 최소한 뜰 수 있는 상태를 만든다:
```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com

docker build -t conservation-backend .
docker tag conservation-backend:latest 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
docker push 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
# 나머지 3개(conservation-guide-ai, xray-ai, pottery-inspection-ai)도 각자 디렉터리에서 동일 패턴 반복
```

---

## 13단계 — `pottery-inspection-ai` 전용 준비 (재사용)

2차 11단계에서 이미 정리된 내용 그대로다 — `ai_hub/pottery_multitask_model_v2`는 git에 커밋돼 있으므로 `Dockerfile`의 `COPY ai_hub/pottery_multitask_model_v2 ./ai_hub/pottery_multitask_model_v2`와 `.dockerignore`의 `ai_hub/*` + `!ai_hub/pottery_multitask_model_v2` 예외 처리가 이미 코드에 반영되어 있다면 이 단계는 확인만 하고 넘어가면 된다.

---

## 14단계 — Secret 생성 (`app-secrets`)

2차 12단계와 동일한 방식이되, **main에 새로 병합된 JWT 인증 기능(`feat: JWT 인증 및 게시판 기능 구현`) 때문에 키 2개가 추가**된다. `.env.k8s`에 RDS 엔드포인트(11단계에서 새로 만든 것), S3 버킷 이름(17단계에서 만들 버킷), JWT 시크릿 등을 채운다.

```env
OPENAI_API_KEY=<값>
POSTGRES_PASSWORD=<값>
AWS_ACCESS_KEY_ID=<값>
AWS_SECRET_ACCESS_KEY=<값>
AWS_S3_BUCKET=<17단계에서 만들 버킷 이름>
AWS_REGION=ap-northeast-2

SPRING_DATASOURCE_URL=jdbc:postgresql://<RDS 엔드포인트>:5432/conservation
SPRING_DATASOURCE_USERNAME=conservation
DATABASE_URL=postgresql://conservation:<POSTGRES_PASSWORD 실제값>@<RDS 엔드포인트>:5432/conservation

JWT_SECRET=<최소 32바이트(256비트) 이상 랜덤 문자열>
JWT_EXPIRATION_MS=3600000
```

> **새로 생긴 필수값(주의)**: `WebSecurityConfig`/`JwtTokenProvider`가 `jwt.secret_key: ${JWT_SECRET}`를 읽어서 `Keys.hmacShaKeyFor(...)`(HS256)로 서명 키를 만든다. 이 값이 없으면 컨테이너가 아예 기동에 실패하고(플레이스홀더 해석 오류), 있어도 **32바이트(256비트)보다 짧으면 로그인 시 `WeakKeyException`이 난다** — 랜덤 문자열을 생성할 때(`openssl rand -base64 32` 등) 이 최소 길이를 지킬 것. `JWT_EXPIRATION_MS`는 `docker-compose.yml`에는 전달되지만 실제 만료 시간은 `application.yaml`의 `jwt.expiration-ms: 3600000`(1시간, 하드코딩)이 적용되므로 값 자체는 크게 중요하지 않다 — 그래도 다른 env 값들과의 일관성을 위해 채워는 둔다.

```bash
kubectl create secret generic app-secrets --from-env-file=.env.k8s
```

> `.env.k8s`를 나중에 고칠 때마다(비밀번호 변경 등) **로컬 파일만 고치는 걸로는 반영 안 된다** — `kubectl delete secret app-secrets && kubectl create secret generic app-secrets --from-env-file=.env.k8s` 후 관련 Deployment `rollout restart`까지 해야 한다(2차에서 직접 겪은 문제).

---

## 15단계 — Deployment/Service/Ingress 매니페스트 작성

Deployment 4개(`conservation-guide-ai`, `xray-ai`, `pottery-inspection-ai`, `conservation-backend`)의 내용은 **2차 `k8s/app.yaml`과 동일**하다 — 서비스 간 통신, EFS 마운트, `SPRING_DATASOURCE_PASSWORD` 개별 매핑 등 전부 그대로 재사용한다.

**바뀌는 건 딱 하나**: `conservation-backend`의 Service를 2차처럼 `type: LoadBalancer`로 바꾸지 않는다. **`type: ClusterIP`로 그대로 두고**, 대신 아래 `Ingress`를 추가한다(ALB는 Ingress가 대신 만들어준다):

```yaml
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

`k8s/app.yaml` 맨 끝에 위 블록을 `---`로 이어붙이면 된다. (`alb.ingress.kubernetes.io/scheme: internet-facing`이 "퍼블릭 서브넷에 ALB 노드를 만들어라"는 뜻이고, `target-type: ip`는 "NodePort를 거치지 않고 Pod IP로 바로 라우팅해라"는 뜻 — 이게 2차의 `type: LoadBalancer` 방식보다 실제 트래픽 경로가 한 홉 짧다.)

---

## 16단계 — 적용 및 검증

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
| `database "conservation" does not exist` | DB 미생성 | 11단계 psql Pod로 `CREATE DATABASE` |
| `ContainerCreating`에 멈춤 | EFS 보안 그룹 2049 누락 | `kubectl describe pod`의 Events |
| `conservation-backend`가 시작하자마자 `Error`(플레이스홀더 관련 예외) | `app-secrets`에 `JWT_SECRET` 누락 (14단계 참고, JWT 기능 병합 이후 새로 필수가 된 값) | `kubectl logs <pod> --previous`에서 `Could not resolve placeholder 'JWT_SECRET'` 류 메시지 확인 |

### Ingress(ALB) 상태 확인
```bash
kubectl get ingress conservation-backend
```
`ADDRESS` 칸에 ALB 도메인이 뜨기까지 1~3분 걸린다. 계속 비어있으면 `kubectl describe ingress conservation-backend`의 Events에서 원인을 본다 — 대부분 1단계의 서브넷 태그 누락(`kubernetes.io/role/elb` 등) 아니면 7단계 컨트롤러 IAM 정책 문제다.

---

## 17단계 — S3 + Lambda: X-ray 업로드 신호

> 이 기능은 기존 코드베이스에 아직 없다(현재 `S3PhotoStorageService.java`는 습윤 효과 테스트 사진용으로 백엔드가 동기 업로드하는 별개 경로다). 아래는 아키텍처가 요구하는 "S3 이벤트 → Lambda → RDS 기록" 배관을 처음부터 새로 만드는 절차이고, Lambda 안의 **실제 비즈니스 로직(어느 테이블의 어느 컬럼을 갱신할지)은 X-ray 쪽 스키마에 맞춰 직접 채워 넣어야 하는 TODO**로 남겨둔다.

### 17-1. S3 버킷 생성
S3 콘솔 → 버킷 만들기 → 이름(전역 유일해야 함, 예: `conservation-xray-uploads-815373273907`) → 리전 `ap-northeast-2` → 퍼블릭 액세스 차단 유지(기본값).

### 17-2. S3 Gateway VPC Endpoint 생성
Lambda가 VPC 안에 들어가면 기본 인터넷 경로를 잃으므로, NAT를 안 거치고 S3 API를 호출하려면 이 엔드포인트가 필요하다.

VPC 콘솔 → 엔드포인트 → 엔드포인트 생성
| 항목 | 값 |
|---|---|
| 서비스 카테고리 | AWS 서비스 |
| 서비스 이름 | `com.amazonaws.ap-northeast-2.s3` (**Gateway** 타입인 것 확인) |
| VPC | `conservation-vpc` |
| 라우팅 테이블 | `private-a-rt`, `private-c-rt` 체크 (프라이빗 컴퓨팅/데이터 서브넷이 쓰는 라우팅 테이블) |
| 정책 | 전체 액세스(기본값) |

### 17-3. Lambda용 보안 그룹
EC2 콘솔 → 보안 그룹 생성: 이름 `lambda-notifier-sg`, VPC `conservation-vpc`, 인바운드 규칙 없음(아웃바운드는 기본 전체 허용 유지).

### 17-4. RDS 보안 그룹에 Lambda 추가 (11단계로 돌아가서)
`conservation-db-sg` 인바운드 규칙 추가: 유형 `PostgreSQL` / 소스 = `lambda-notifier-sg` (드롭다운에서 선택).

### 17-5. Lambda 실행 역할
IAM 콘솔 → 역할 생성 → 서비스: Lambda → 정책: **`AWSLambdaVPCAccessExecutionRole`**(ENI 생성/삭제 + CloudWatch Logs 쓰기, VPC에 연결된 Lambda라면 필수) → 이름 `xray-upload-notifier-role`.

(S3 이벤트가 Lambda를 호출하는 권한은 IAM 역할이 아니라 Lambda의 **리소스 기반 정책**으로 별도 관리되며, 콘솔에서 트리거를 추가하면 자동으로 붙는다.)

### 17-6. Lambda 컨테이너 이미지 작성
`lambda/xray-upload-notifier/Dockerfile`:
```dockerfile
FROM public.ecr.aws/lambda/python:3.12
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY handler.py .
CMD ["handler.handler"]
```

`lambda/xray-upload-notifier/requirements.txt`:
```
psycopg2-binary
```

`lambda/xray-upload-notifier/handler.py` (골격 — 마지막 UPDATE 문은 실제 테이블/컬럼명에 맞춰 직접 바꿀 것):
```python
import json
import os
import psycopg2

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]


def handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        print(f"S3 업로드 감지: s3://{bucket}/{key}")

        # TODO: key에서 job_id/task_id를 파싱해서 실제 X-ray 작업 테이블의
        # 상태 컬럼을 갱신하는 로직을 채워 넣을 것. 아래는 예시일 뿐,
        # 실제로 이런 테이블/컬럼이 존재하지 않으므로 그대로 쓰면 안 됨.
        conn = psycopg2.connect(
            host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE xray_jobs SET upload_status = %s WHERE s3_key = %s",
                    ("uploaded", key),
                )
            conn.commit()
        finally:
            conn.close()

    return {"statusCode": 200, "body": json.dumps({"processed": len(event["Records"])})}
```

빌드 및 push:
```bash
cd lambda/xray-upload-notifier
docker build -t xray-upload-notifier .
docker tag xray-upload-notifier:latest 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/xray-upload-notifier:latest
docker push 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/xray-upload-notifier:latest
```

### 17-7. Lambda 함수 생성
Lambda 콘솔 → 함수 생성 → **컨테이너 이미지** → 이미지: 방금 push한 `xray-upload-notifier:latest` → 실행 역할: `xray-upload-notifier-role`

생성 후 **구성** 탭에서:
- **VPC**: `conservation-vpc`, 서브넷 `private-compute-a` + `private-compute-c` 둘 다, 보안 그룹 `lambda-notifier-sg`
- **환경 변수**: `DB_HOST`(RDS 엔드포인트), `DB_NAME=conservation`, `DB_USER=conservation`, `DB_PASSWORD`(마스터 암호)

### 17-8. S3 트리거 연결
Lambda 콘솔 → 함수 → **트리거 추가** → S3 → 버킷: 17-1에서 만든 버킷 → 이벤트 유형: `PUT`(객체 생성) → 저장. (콘솔이 S3 버킷 알림 설정 + Lambda 리소스 기반 정책 부여를 자동으로 처리한다.)

### 확인
S3 버킷에 아무 이미지 파일이나 콘솔로 직접 업로드해보고, Lambda 콘솔 → **모니터링 → CloudWatch 로그**에서 `S3 업로드 감지: s3://...` 로그가 찍히는지, RDS 연결까지 성공하는지 확인한다.

---

## 18단계 — CI/CD 파이프라인 (CodeCommit → CodePipeline → CodeBuild → ECR)

2차 "남은 작업" 3번을 여기서 구현한다.

> **주의**: AWS CodeCommit은 2024년 7월부터 **신규 리포지토리 생성이 기존 사용 이력이 없는 계정에서는 막혀 있을 수 있다**(AWS가 신규 고객에게는 더 이상 CodeCommit을 권하지 않는 방향으로 정책을 바꿨다). 계정에서 CodeCommit 콘솔에 들어가서 "리포지토리 생성" 버튼이 비활성화되어 있거나 안내 문구만 뜬다면, 소스 저장소는 GitHub을 그대로 쓰고 **CodeStar Connections**로 CodePipeline과 연결하는 방식으로 대체해야 한다(그 경우 다이어그램의 CodeCommit 자리만 GitHub+CodeStar Connections로 바뀌고 뒤 단계는 동일). 진행 전에 콘솔에서 먼저 확인할 것.

### 18-1. CodeCommit 리포지토리 생성 (또는 위 대체 경로)
CodeCommit 콘솔 → 리포지토리 생성 → 이름 `conservation-backend` → 로컬 저장소에 원격 추가 후 push:
```bash
git remote add codecommit <CodeCommit에서 안내하는 clone URL>
git push codecommit main
```

### 18-2. CodeBuild 프로젝트 생성
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

### 18-3. (선택) EKS까지 자동 배포
`buildspec.yml`의 `post_build` 뒤에 아래를 더 붙이면 이미지 push까지뿐 아니라 실행 중인 Pod도 자동으로 새 이미지로 교체된다. 다만 이러려면 **CodeBuild의 서비스 역할에도 5단계와 같은 EKS Access Entry를 만들어줘야 한다**(`AmazonEKSClusterAdminPolicy`까지는 과하고, 배포용으로 범위를 좁힌 정책을 쓰는 걸 권장):
```yaml
      - curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
      - chmod +x kubectl && mv kubectl /usr/local/bin/
      - aws eks update-kubeconfig --name conservation-cluster --region $AWS_REGION
      - kubectl rollout restart deployment/conservation-backend deployment/conservation-guide-ai deployment/xray-ai deployment/pottery-inspection-ai
```
자동화가 아직 부담스럽다면 이 부분은 생략하고, 2차처럼 이미지 push까지만 자동화한 뒤 `kubectl rollout restart`는 계속 수동으로 해도 된다 — 그 경우 CI(빌드/푸시)까지만 자동, CD(배포)는 수동인 상태로 남는다는 걸 팀 안에서 인지하고 있으면 된다.

### 18-4. CodePipeline 생성
CodePipeline 콘솔 → 파이프라인 생성
| 스테이지 | 공급자 | 설정 |
|---|---|---|
| Source | CodeCommit | 리포지토리 `conservation-backend`, 브랜치 `main` |
| Build | CodeBuild | 프로젝트 `conservation-build` |

생성 후 `main`에 push할 때마다 파이프라인이 자동 실행된다.

---

## 19단계 — 외부 접속 확인

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

## 20단계 — 비용 참고

2차 대비 새로 생기거나 늘어나는 과금 요소:
| 항목 | 비고 |
|---|---|
| NAT Gateway ×2 | 시간당 요금 + 처리한 데이터(GB)당 요금, 2차엔 아예 없던 항목 |
| RDS Multi-AZ | Single-AZ 대비 대략 2배 (Standby 인스턴스 비용) |
| ALB | 시간당 요금 + LCU(트래픽량) 요금 — 2차의 Service `LoadBalancer`(NLB류)와 요금 체계가 다름 |
| Lambda | 호출 횟수/실행시간 기준, 업로드 이벤트마다 소량 과금(프리티어 월 100만 건까지 무료라 이 프로젝트 규모에선 사실상 무료) |
| VPC Endpoint(S3, Gateway 타입) | 무료 |

정지/축소 시 주의할 점(2차에서 다룬 것과 동일한 원리): EKS 노드 그룹을 0으로 줄여도 **NAT Gateway·ALB·RDS는 별도로 끄지 않는 한 계속 과금**된다. 완전히 멈추고 싶다면 노드 그룹 축소 + RDS 중지(최대 7일) + (필요시) NAT Gateway/ALB 자체를 삭제해야 한다 — 다만 NAT Gateway·서브넷·라우팅 테이블을 지우면 이 아키텍처를 다시 쓸 때 1단계부터 재구성해야 하니, 단기 중단이면 EKS 축소 + RDS 중지 정도로 충분하다.

---

## 21단계 — 남은 작업

1. **Lambda 비즈니스 로직 완성**: 17단계의 `handler.py`는 골격뿐이다. 실제 X-ray 작업 상태를 추적하는 테이블/컬럼을 설계하고 그에 맞춰 `UPDATE` 문(또는 `conservation-backend`를 호출하는 방식으로 전환)을 채워야 한다.
2. **Lambda → EC2 알림 경로 재검토**: 지금은 Lambda가 RDS에 직접 쓰기만 한다. `xray-ai`/`conservation-backend`가 이 상태 변화를 실시간으로 알아야 한다면(폴링이 아니라 즉시 알림이 필요하다면) Lambda에서 ALB(내부 경로) 또는 내부 API 호출을 추가하는 걸 고려.
3. **IRSA로 S3 접근 전환**: `conservation-backend`가 여전히 `.env.k8s`의 정적 `AWS_ACCESS_KEY_ID`/`SECRET`으로 S3에 접근 중이면, ServiceAccount 기반 IAM 역할로 전환 검토(2차부터 이어지는 미해결 항목).
4. **HTTPS**: 지금 ALB는 `HTTP` 80 포트만 연다. 도메인을 붙이고 ACM 인증서를 발급해서 `HTTPS` 리스너를 추가하는 게 다음 단계.
5. **CodeCommit 대체 경로 확정**: 18단계 서두의 주의사항대로, 계정에서 CodeCommit이 막혀 있다면 GitHub + CodeStar Connections로 소스 스테이지를 바꿔야 한다.
