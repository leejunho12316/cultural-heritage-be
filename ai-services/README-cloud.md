# AWS ECR + EKS 배포 작업 로그

이 문서는 `conservation_backend` 프로젝트(Spring `conservation-backend` + FastAPI `conservation-guide-ai` + FastAPI `xray-ai` + Postgres)를 AWS ECR / EKS로 배포하기 위해 **AWS Console(GUI)에서 진행한 모든 작업**을 순서대로 기록한다. CLI(`eksctl`, `kubectl` 등)가 아니라 콘솔 클릭 기준으로 정리되어 있다.

## 전체 아키텍처 개요

- `conservation-backend` (Spring, 8080) — 모든 요청의 관문, 유일하게 외부에 노출
- `conservation-guide-ai` (FastAPI, 8000) — LangGraph 기반 보존처리 가이드 AI
- `xray-ai` (FastAPI, 8001) — X-RAY 조각 결합/이상영역 탐지
- `postgres` (16) — LangGraph 체크포인터 + 앱 DB
- `shared/` — `conservation-backend`와 `xray-ai`가 결합 작업 파일(이미지)을 주고받는 공유 볼륨

기존 `docker-compose.yml` 기준 3개 서비스 이미지를 ECR에 올리고, EKS 클러스터에서 각각 별도 Deployment로 띄우는 것이 목표.

### 아키텍처 결정 사항 (논의 후 확정)

- **3개 서비스(conservation-backend / conservation-guide-ai / xray-ai)는 하나의 Pod로 합치지 않고, 별도 Deployment 3개로 유지한다.**
  - 이유: 리소스 프로파일이 다름(xray-ai는 torch/YOLO로 무거움, backend는 가벼움), 독립적 스케일링 필요, 독립적 배포(하나만 이미지 업데이트 시 나머지 무중단 유지) 필요, `emptyDir`은 Pod 재스케줄 시 데이터가 소실되는 반면 EFS는 유지됨.
- **`shared/` 폴더는 EFS(ReadWriteMany)로 재현한다.** EBS는 `ReadWriteOnce`라 Pod 하나만 마운트 가능해서 두 서비스가 동시에 못 쓴다.
- **Postgres는 RDS로 최종 결정.** 이유: EBS PVC 방식은 `Amazon EBS CSI Driver` 애드온을 EFS 때와 동일한 IAM 역할/Pod Identity 설정 과정을 한 번 더 거쳐야 해서 번거로움. RDS는 `db.t3.micro` 단일 AZ 기준 프리티어로 12개월 무료라 비용 차이도 없어서 RDS 채택.

---

## 1단계 — ECR (Elastic Container Registry)

### 사전 확인
- 리전은 `.env`의 `AWS_REGION` 기본값과 동일하게 **`ap-northeast-2`(서울)**로 통일.

### 리포지토리 생성 (3개)
ECR 콘솔 → Repositories → **Create repository**, 아래 설정으로 3번 반복:

| 항목 | 값 |
|---|---|
| Visibility | Private |
| Repository name | `conservation-backend` / `conservation-guide-ai` / `xray-ai` |
| Tag immutability | Mutable (기본값 유지) |
| Scan on push | Enable |
| Encryption | AES-256 (기본값) |

### 푸시 명령
각 리포지토리 상세 화면 → **View push commands** 버튼 → 아래 4줄이 계정ID/리전 채워진 채로 표시됨:
```bash
aws ecr get-login-password --region <리전> | docker login --username AWS --password-stdin <계정ID>.dkr.ecr.<리전>.amazonaws.com
docker build -t <repo-name> .
docker tag <repo-name>:latest <계정ID>.dkr.ecr.<리전>.amazonaws.com/<repo-name>:latest
docker push <계정ID>.dkr.ecr.<리전>.amazonaws.com/<repo-name>:latest
```

**중요**: `docker build -t <repo-name> .`의 `.`(빌드 컨텍스트)는 **서비스별 Dockerfile이 있는 디렉토리에서 실행**해야 한다. 한 디렉토리에서 3번 다 실행하면 3개 리포지토리에 같은 이미지가 잘못 올라간다.

| 서비스 | Dockerfile 위치 |
|---|---|
| conservation-backend | 레포 루트 `/Dockerfile` |
| conservation-guide-ai | `/ai-services/conservation-guide-ai/Dockerfile` |
| xray-ai | `/ai-services/xray-ai/Dockerfile` |

### 필요한 IAM 권한 (push용)
- push를 실행하는 IAM 사용자에게 **`AmazonEC2ContainerRegistryPowerUser`** 관리형 정책 부여.
- 정책 이름에 `EC2Container`가 들어있지만 이건 ECR의 역사적 이름 잔재이며, **실제 EC2 인스턴스 권한과는 무관**하다 (헷갈리기 쉬운 포인트로 논의됨).

### 실제 진행한 방식 (사용자 실행)
1. ECR 서비스 들어가서 프라이빗 레포지토리 3개 생성 (BE, 보존 가이드, XRAY)
2. IAM 사용자 생성 + ECR push 관련 권한(`AmazonEC2ContainerRegistryPowerUser` 계열) 부여, 액세스 키 발급
3. `aws configure`로 AWS CLI 로그인
4. 각 프라이빗 레포지토리에서 "푸시 명령 보기" → 명령어 4개 실행하여 Docker 이미지 업로드

### 검증 결과
ECR 콘솔에서 각 리포지토리의 **Images** 탭 → 이미지 크기(MB) 확인:

| 서비스 | 이미지 크기 |
|---|---|
| conservation-backend | 171.79 MB |
| conservation-guide-ai | 560.92 MB |
| xray-ai | 599.47 MB |

세 값이 모두 다르므로 각각 올바른 이미지가 정상적으로 push된 것으로 확인됨.

### Images 탭에 3줄씩 뜨는 이유 (정상 동작)
각 리포지토리의 Images 탭에 아래처럼 3개 행이 뜨는데, 이는 **이미지가 3개 올라간 게 아니라 논리적으로 1개 이미지**다:
- **Image Index** (태그 `latest` 붙음, 실제 이미지 크기와 동일): 실제 이미지를 가리키는 매니페스트 목록(포인터)
- **Image** (태그 없음, 실제 이미지 크기와 동일): Image Index가 가리키는 진짜 이미지 레이어
- **Image** (태그 없음, 크기 0.00MB): **provenance/SBOM(공급망 증명) 메타데이터** — 최신 Docker buildx가 기본으로 push하는 부가 정보. 실제 이미지 내용 아님.

예시 (conservation-backend):
```
latest | Image Index | 2026-08-03 11:07:12 | 171.79MB | sha256:b89c4e5c5ff4b52093d88a5b2032e9e14d1dbcbc5bd4c3201762a65a9b314819
       | Image        | 2026-08-03 11:07:12 | 0.00MB   | sha256:1d2dbd27658eb64b842b8886ea2c9cd5cc10003469713aadfacedbc946e94633
       | Image        | 2026-08-03 11:07:12 | 171.79MB | sha256:fca115b1a4985bfb2c7c6a1fa8797beb7de476760060607645fe1459df0171fb
```
conservation-guide-ai, xray-ai도 동일한 구조(Image Index + Image 560.92MB/599.47MB + Image 0.00MB).

---

## 2단계 — IAM 역할 준비

### 왜 필요한가
ECR push용으로 만든 IAM 사용자/역할은 **"사람(로컬 CLI)"**이 쓰는 것이고, EKS를 굴리려면 **"AWS 서비스/EC2 노드 자신"**이 다른 AWS 리소스에 접근할 별도 역할이 최소 2개 필요하다.

| 구분 | 누가 쓰나 | 용도 |
|---|---|---|
| ECR push용 역할(1단계) | 사람(개발자, CLI) | 로컬에서 `docker push` |
| **eksClusterRole** | AWS EKS 서비스 자체 | 클러스터를 대신 관리 (ENI 생성, 로드밸런서 연동 등) |
| **eksNodeRole** | EC2 워커 노드(서버) | ECR에서 이미지 pull, EKS API와 통신 |

### eksClusterRole 생성
IAM 콘솔 → 역할(Roles) → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `AWS 서비스`
2. 사용 사례(Use case): `EKS` 검색 → **EKS - Cluster** 선택 (신뢰 정책에 `eks.amazonaws.com`이 자동으로 들어감)
3. 권한 정책: **`AmazonEKSClusterPolicy`** (자동 선택됨, 없으면 직접 체크)
4. 역할 이름: **`eksClusterRole`**

### eksNodeRole 생성
IAM 콘솔 → 역할(Roles) → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `AWS 서비스`
2. 사용 사례: `EC2` 선택
3. 권한 정책 3개 모두 체크:
   - **`AmazonEKSWorkerNodePolicy`** — 노드가 EKS 컨트롤 플레인과 통신
   - **`AmazonEKS_CNI_Policy`** — 파드에 IP 할당하는 VPC CNI 네트워킹 플러그인
   - **`AmazonEC2ContainerRegistryReadOnly`** — ECR 리포지토리 3개에서 이미지 pull (push용과 다른 점: 노드는 pull만 필요)
4. 역할 이름: **`eksNodeRole`**

---

## 3단계 — EKS 클러스터 + 노드 그룹 생성

### 사전 확인: VPC/서브넷
- EKS는 **서로 다른 가용영역(AZ) 2개 이상에 걸친 서브넷**이 필요하다.
- **주의**: 서브넷 자체가 여러 AZ에 걸치는 게 아니다 — AWS에서 서브넷은 반드시 하나의 AZ에만 속한다. "AZ 2개 이상에 걸친 서브넷 구성"이란, **서로 다른 AZ에 있는 서브넷을 여러 개 선택**해서 그 집합이 2개 이상의 AZ를 커버하게 만드는 것이다.
- 기본(default) VPC는 리전 안 AZ마다 서브넷을 자동으로 하나씩 만들어두므로 그대로 사용.
- 확인된 VPC: **`vpc-014228e3218853231`**

### 클러스터 생성
EKS 콘솔 → 클러스터(Clusters) → **클러스터 생성**
- 상단 모드 토글: **`Custom configuration`** 선택 (Auto Mode 아님 — Auto Mode는 AWS가 노드그룹/스토리지/로드밸런싱을 자동 관리하는 방식이라, 직접 `eksNodeRole`을 쓰는 학습 목적과 안 맞아서 배제)

**Configure cluster**
| 항목 | 값 |
|---|---|
| Name | `conservation-cluster` |
| Kubernetes version | 기본 최신 |
| Cluster service role | `eksClusterRole` |

**Specify networking**
| 항목 | 값 |
|---|---|
| VPC | 기본 VPC (`vpc-014228e3218853231`) |
| Subnets | 최소 2개 AZ 이상 (실제로는 `ap-northeast-2a/b/c/d` 4개 AZ 서브넷 전부 선택) |
| Cluster endpoint access | `Public` |

**Select add-ons**: 기본값(VPC CNI, CoreDNS, kube-proxy) 그대로 유지.

**Review and create** → Create. 생성에 10~15분 소요.

### 생성 중 뜬 경고 (무시해도 됨)
클러스터 역할(`eksClusterRole`)에 대해 다음 경고가 표시됨:
> 클러스터 역할에 권장 관리형 정책이 없음
> EKS 자율 모드를 사용하려면 클러스터 역할에 다음 관리형 정책 또는 이와 동등한 권한이 있어야 합니다.
> `AmazonEKSBlockStoragePolicyV2`, `AmazonEKSComputePolicy`, `AmazonEKSLoadBalancingPolicy`, `AmazonEKSNetworkingPolicy`
> 클러스터 역할 신뢰 정책 누락 필요한 작업: `sts:TagSession`

→ 이건 **"EKS 자율 모드(Auto Mode)를 쓸 경우에만"** 필요한 조건부 안내. `Custom configuration`을 선택했으므로 무시하고 진행. (Auto Mode였다면 필수였을 정책들 — EBS 볼륨/EC2 인스턴스/로드밸런서/네트워킹을 클러스터 역할이 자동으로 대신 만들 때 쓰는 권한)

### 노드 그룹 추가
클러스터 상태가 `Active`가 된 후: 클러스터 상세 → **컴퓨팅(Compute)** 탭 → **노드 그룹 추가**

| 항목 | 값 |
|---|---|
| Name | `workers` |
| Node IAM role | `eksNodeRole` |
| AMI type | Amazon Linux 2 (x86) |
| Instance type | `t3.medium` |
| Disk size | 기본값(20GB) |
| Desired size | 2 |
| Minimum size | 1 |
| Maximum size | 4 |
| Subnets | 클러스터와 동일 |

### 완료 확인
콘솔: 노드 그룹 상태 `Active`, Nodes 목록에 EC2 인스턴스 2대 `Ready`.
CLI:
```bash
aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2
kubectl get nodes
```

### 트러블슈팅 — 프리티어 계정에서 t3.medium 노드 그룹 생성 실패
최초 `t3.medium`으로 노드 그룹을 생성했을 때 아래 문제로 **노드 그룹 생성 자체가 실패**함:

| 항목 | 내용 |
|---|---|
| 문제 유형 | `AsgInstanceLaunchFailures` |
| 설명 | `Could not launch On-Demand Instances. InvalidParameterCombination - The specified instance type is not eligible for Free Tier. For a list of Free Tier instance types, run 'describe-instance-types' with the filter 'free-tier-eligible=true'. Launching EC2 instance failed.` |
| 영향받은 리소스 | `eks-worwkers-d8cfe2ed-a3d9-8679-f1b5-3ffc5eb255c4` |

**원인**: AWS 프리티어(Free Tier) 계정이라 `t3.medium`은 launch가 거부됨.

**후속 증상**: 이 상태에서 EFS CSI 드라이버 애드온을 설치하면 상태가 **`저하됨(Degraded)`**으로 뜨고, 상세 화면에 아래 문제가 표시됨:
```
InsufficientNumberOfReplicas
The add-on is unhealthy because all deployments have all pods unscheduled no nodes available to schedule pods
```
→ IAM/Pod Identity 설정 문제가 아니라, **스케줄링할 노드가 아예 없어서** 발생한 것.

**해결**:
1. 실패한 `workers` 노드 그룹 삭제
2. 동일한 설정으로 재생성하되 **Instance type을 `c7i-flex.large`로 변경**(최초 시도한 `t3.micro`보다 스펙이 높은데도 이 계정에서는 프리티어로 사용 가능했음), 나머지(Node IAM role: `eksNodeRole`, Desired 2 / Min 1 / Max 4, 서브넷)는 동일하게 유지
3. 재생성 후 노드 `Ready` 확인 → EFS CSI 드라이버 애드온 상태도 자동으로 `Active`로 전환됨

**참고**: 프리티어로 허용되는 인스턴스 타입은 계정/리전마다 다를 수 있음 — `t3.medium`은 거부됐지만 `c7i-flex.large`는 허용됨. 실제로 노드 그룹 생성 시 어떤 타입이 프리티어로 되는지는 시도해보며 확인 필요. `c7i-flex.large`는 `t3.micro`보다 스펙이 높아, 나중에 애플리케이션(`xray-ai`의 torch/YOLO 포함) 배포 시에도 별도 인스턴스 타입 재변경 없이 진행 가능할 것으로 예상.

---

## 개념 정리 (진행 중 나온 질문들)

### Deployment란
"이 이미지로 컨테이너를 N개 계속 띄워놓고 유지해줘"라고 선언하는 K8s 오브젝트. Pod가 죽으면 자동 재생성, 이미지 업데이트 시 무중단 순차 교체(rolling update) 지원. docker-compose의 `services:`와 유사하지만 복구/스케일까지 자동 관리.

### PersistentVolumeClaim(PVC)란
"저장공간 얼마 필요해"라고 요청하는 오브젝트. 컨테이너는 기본적으로 상태가 없어(stateless) Pod 재시작 시 내부에 쓴 파일이 사라지므로, Postgres처럼 데이터를 유지해야 하는 경우 PVC로 별도 저장공간을 연결해야 한다.

### EBS vs EFS
- **EBS (Elastic Block Store)**: 컴퓨터 한 대에 꽂는 외장하드. **한 번에 하나의 Pod만** 마운트 가능(`ReadWriteOnce`). Postgres처럼 인스턴스 하나가 독점하는 경우에 적합.
- **EFS (Elastic File System)**: 네트워크로 연결된 공유 폴더. **여러 Pod가 동시에** 마운트 가능(`ReadWriteMany`). `shared/` 처럼 여러 서비스가 같은 파일을 주고받는 경우에 필요.

### docker-compose vs K8s Pod
docker-compose와 K8s는 완전히 별개의 도구. 같은 Docker 이미지(예: `postgres:16`)를 docker-compose가 실행하면 "컨테이너"가 되고, K8s가 실행하면 "Pod 안의 컨테이너"가 된다. compose 파일 자체가 K8s에서 재사용되는 게 아니라 같은 이미지를 다른 도구가 실행하는 것.

### 하나의 Pod에 여러 컨테이너를 넣으면 안 되나 (3-in-1 검토 후 기각)
기술적으로는 Pod 안 컨테이너들이 네트워크(localhost)와 볼륨(`emptyDir`)을 공유할 수 있어서, backend/guide-ai/xray-ai를 한 Pod로 합치면 EFS 없이도 `shared/` 문제가 해결된다. 하지만:
1. 리소스 프로파일이 달라 개별 리소스 요청/제한 불가
2. 독립적 스케일링 불가 (하나 늘리면 셋 다 같이 늘어남)
3. 독립적 배포 불가 (하나만 업데이트해도 Pod 전체 재시작 필요)
4. `emptyDir`은 Pod가 다른 노드로 재스케줄되면 데이터가 소실됨 (EFS는 유지됨)

→ **3개 서비스는 별도 Deployment로 유지하고 EFS를 쓰는 쪽으로 최종 결정.**

RDS는 이 논의와 별개로, Pod를 어떻게 나누든 상관없이 항상 필요한 결정 사항 (DB는 영구 저장이 필수라 `emptyDir`로 대체 불가).

### 매니페스트(Manifest)란
**"클러스터가 이런 상태였으면 좋겠다"를 적어놓은 YAML 설계도.** `docker-compose.yml`이 "이런 컨테이너들을 이렇게 띄워줘"를 선언하는 파일이었다면, K8s 매니페스트(`k8s/storage.yaml`, `k8s/app.yaml`)는 그 K8s 버전이다.

`kubectl apply -f 파일.yaml`을 실행하면:
1. kubectl이 YAML 내용을 읽어서
2. "Deployment 이름 X, 이미지는 Y, replica는 N개..." 같은 **원하는 상태(desired state)**를 EKS API 서버에 전송
3. K8s가 "지금 상태 vs 원하는 상태"를 비교해서 부족한 건 만들고 나머지는 그대로 둠

이런 방식을 **선언적(declarative)** 방식이라 부른다 — "이렇게 해줘"(명령형)가 아니라 "이런 상태여야 해"(선언형)로 요청하는 것. 그래서 같은 매니페스트를 여러 번 다시 `apply`해도 안전하다 (이미 그 상태면 아무것도 안 바뀜).

### kubectl은 로컬에서 도는가, EKS가 로컬에서 도는가
**Kubernetes 자체는 로컬 컴퓨터에 전혀 없다.** 실제로 돌아가는 곳은 AWS의 EKS 컨트롤 플레인(관리형)과 워커 노드 EC2 인스턴스 2대 — 전부 AWS 서버.

`kubectl`은 로컬에 설치된 **클라이언트 프로그램일 뿐**이며, 인터넷을 통해 EKS API 서버(공개 엔드포인트)에 HTTPS 요청을 보내는 역할만 한다.

```
kubectl(로컬) → (AWS CLI로 자격증명 기반 토큰 생성) → 인터넷 → EKS API 서버(AWS 원격)
```

### `~/.kube/config`(Windows 실경로: `C:\Users\User\.kube\config`)와 `aws eks update-kubeconfig`가 하는 일

`aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2` 실행 시 이 파일에 4가지가 자동으로 써짐:

```yaml
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: <서버 인증서, 공개정보>
    server: https://93A439E84E8E23F9A1D97A8E222A5901.sk1.ap-northeast-2.eks.amazonaws.com
  name: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
contexts:
- context:
    cluster: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
    user: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
  name: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
current-context: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
kind: Config
preferences: {}
users:
- name: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - --region
      - ap-northeast-2
      - eks
      - get-token
      - --cluster-name
      - conservation-cluster
      - --output
      - json
      command: aws
```

- **`clusters`**: EKS API 서버의 실제 주소(`server`)와, 이 서버가 진짜 AWS 서버가 맞는지 검증하는 인증서(`certificate-authority-data`, 비밀값 아님).
- **`users` (핵심)**: `kubectl`은 자체 로그인 시스템이 없다. 요청을 보내기 직전마다 **`aws eks get-token --cluster-name conservation-cluster` 명령을 내부적으로 직접 실행**해서, `aws configure`로 등록해둔 AWS 자격증명을 이용해 **짧은 시간만 유효한 인증 토큰**을 만들고, 그 토큰을 API 서버에 제출한다.
- **`contexts`**: `cluster`(어디)와 `user`(누구로 인증할지)를 하나로 묶은 것.
- **`current-context`**: 지금 기본으로 쓸 조합. 클러스터가 여러 개면 `kubectl config use-context <이름>`으로 전환.

정리: `update-kubeconfig`는 "이 클러스터에 이 AWS 자격증명으로 접속해라"는 접속 정보를 로컬 파일에 저장해준 것이고, 이후 모든 `kubectl` 명령은 이 파일을 보고 **매번 새로 인증**해서 EKS API 서버와 통신한다.

---

## 4단계 — EFS(Elastic File System) 생성

### `shared/` 폴더의 로컬 동작 방식
`docker-compose.yml`에서 `conservation-backend`와 `xray-ai` 양쪽에 `./shared:/shared`가 bind mount되어 있음. Spring이 업로드 파일을 `/shared/jobs/{jobId}/`에 저장하면, xray-ai 컨테이너도 같은 호스트 디스크를 보고 있어서 바로 그 파일이 보인다. 그래서 Spring이 xray-ai를 호출할 때 파일 자체가 아니라 **경로만** 넘겨도 동작한다.

EKS에서는 `conservation-backend` Pod와 `xray-ai` Pod가 서로 다른 EC2 노드에 뜰 수 있어 로컬 디스크 공유가 불가능 → EFS로 네트워크 공유 스토리지를 재현해야 함.

### 파일 시스템 생성
EFS 콘솔 → **파일 시스템 생성**
| 항목 | 값 |
|---|---|
| Name | `conservation-shared` |
| VPC | EKS 클러스터와 동일 (`vpc-014228e3218853231`) |
| 가용성 및 내구성 | Regional (멀티 AZ) |

생성 시 자동으로 각 서브넷(AZ)마다 마운트 대상(Mount target)이 생성됨.

### 마운트 대상(Mount Target) 개념
EFS 파일 시스템 자체는 VPC 밖의 AWS 관리 네트워크에 있음. 워커 노드가 접근하려면 VPC 안에 "문"이 필요한데 이게 마운트 대상 — 실제로는 서브넷마다 하나씩 생기는 ENI(네트워크 인터페이스, IP 주소 하나)다. 노드가 있는 AZ마다 마운트 대상이 있어야 그 AZ의 노드가 EFS에 접근 가능.

### 실제 생성된 마운트 대상 (4개, AZ별)

| AZ | 탑재 대상 ID | 서브넷 ID | IPv4 | 네트워크 인터페이스 ID |
|---|---|---|---|---|
| ap-northeast-2a (apne2-az1) | `fsmt-0dcac5139ec0b8207` | `subnet-045ccd12051835fd4` | `172.31.8.194` | `eni-02860be5e372e6c75` |
| ap-northeast-2b (apne2-az2) | `fsmt-0d064644e6aa3afdc` | `subnet-06a378d2028f354e0` | `172.31.16.51` | `eni-09d22d7d7a1d8d5de` |
| ap-northeast-2c (apne2-az3) | `fsmt-0ba1133a1bc16a371` | `subnet-047353cd440afcf01` | `172.31.32.109` | `eni-0321e183cf82eb576` |
| ap-northeast-2d (apne2-az4) | `fsmt-04dc288c59338c22c` | `subnet-0a73b03b06c8e8611` | `172.31.59.126` | `eni-063d460cf8c71bba0` |

VPC ID: `vpc-014228e3218853231` (전체 공통)

### 보안 그룹 — NFS(2049) 포트 허용
1. EFS 콘솔 → 파일 시스템 → **네트워크(Network)** 탭 → 표의 **보안 그룹** 컬럼에서 `sg-xxxxxxxx` 값 확인 (콘솔 버전에 따라 클릭 가능한 링크가 아닐 수 있음 — 이 경우 값을 복사해서 EC2 콘솔에서 직접 검색)
2. EC2 콘솔 → **네트워크 및 보안 → 보안 그룹** → 해당 `sg-xxxxxxxx` 검색 후 클릭
3. **인바운드 규칙 편집** → 규칙 추가:
   - 유형: `NFS`
   - 프로토콜/포트: `TCP 2049` (자동 설정)
   - 소스: **eksNodeRole을 쓰는 워커 노드의 보안 그룹**
4. 저장

(참고: 마운트 대상 생성 직후에는 "탑재 대상 상태"가 `생성 중`이고 보안 그룹 컬럼이 `-`으로 비어있음 — 1~2분 후 `사용 가능`(Available)으로 바뀌면 값이 채워짐.)

### 코드 수정 필요 여부 — 불필요함 (확인 완료)
Spring/`xray-ai` 코드 모두 `/shared` 경로를 **환경변수**로만 참조하고 있어, 뒤에 EFS가 붙든 로컬 디스크가 붙든 애플리케이션 코드/설정값은 그대로 재사용 가능. K8s 매니페스트에서 컨테이너 마운트 경로만 기존과 동일하게 `/shared`로 지정하면 됨.

확인된 코드 위치:
- `src/main/resources/application.yaml` (39-43번 줄):
  ```yaml
  storage:
    # Spring Boot 컨테이너가 입력 ZIP과 job.json을 저장하는 경로
    local-root: ${XRAY_STORAGE_LOCAL_ROOT:./shared/jobs}
    # FastAPI 컨테이너에 전달할 동일 공유 볼륨 경로
    container-root: ${XRAY_STORAGE_CONTAINER_ROOT:/shared/jobs}
  ```
- `ai-services/xray-ai/app/config.py` (234-239번 줄, 268-272번 줄):
  ```python
  # Spring Boot와 FastAPI가 함께 사용하는 작업 루트
  STITCH_JOBS_ROOT = Path(
      os.getenv("XRAY_STITCH_JOBS_ROOT", "/shared/jobs")
  ).resolve()
  ...
  os.getenv("XRAY_STITCH_CACHE_DIR", "/shared/cache")
  ```

K8s 매니페스트 예시 (개념):
```yaml
volumeMounts:
  - name: shared-storage
    mountPath: /shared        # 코드가 기대하는 경로 그대로
volumes:
  - name: shared-storage
    persistentVolumeClaim:
      claimName: shared-efs-pvc   # 뒤에서 EFS로 연결됨
```

---

## 5단계 — OIDC 자격 증명 공급자 연동

### 왜 필요한가
EFS CSI 드라이버(Pod)가 AWS API(EFS 마운트)를 호출하려면 전용 IAM 역할이 필요하고, Pod가 그 역할을 assume하려면 클러스터에 OIDC 공급자가 IAM에 등록되어 있어야 한다 (IRSA 방식).

### 진행
1. EKS 콘솔 → 클러스터 → **개요(Overview)** 탭에서 **OpenID Connect provider URL** 확인:
   ```
   https://oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901
   ```
2. IAM 콘솔 → 왼쪽 사이드바 **ID 제공업체** (콘솔 버전에 따라 "자격 증명 공급자"로도 표시됨) → **공급자 추가**
   | 항목 | 값 |
   |---|---|
   | 공급자 유형 | `OpenID Connect` |
   | 공급자 URL | `https://oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901` |
   | 대상(Audience) | `sts.amazonaws.com` |
3. **공급자 추가** 클릭.

**참고**: 예전 방식은 "지문 가져오기(Get thumbprint)" 버튼이 별도로 있었으나, 최신 콘솔에서는 이 단계가 없어지고 AWS가 자동으로 인증서 지문을 검증/등록한다.

### IAM 콘솔 좌측 메뉴 전체 목록 (확인됨, 참고용)
```
대시보드
액세스 관리
  역할
  정책
  IAM 사용자
  IAM 사용자 그룹
  ID 제공업체
계정 설정
루트 액세스 관리
임시 위임 요청
보고서 액세스
Access Analyzer
  리소스 분석
  미사용 액세스
  분석기 설정
정책 시뮬레이터
자격 증명 보고서
조직 활동
서비스 제어 정책
리소스 제어 정책
```

---

## 6단계 — EFS CSI 드라이버용 IAM 역할 생성

### 역할 생성 (IRSA 방식으로 시작)
IAM 콘솔 → 역할 → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `웹 자격 증명(Web identity)`
2. 자격 증명 공급자: 5단계에서 등록한 `oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901` 선택
3. 대상(Audience): `sts.amazonaws.com`
4. 권한 정책: **`AmazonEFSCSIDriverPolicy`**
5. 역할 이름: **`AmazonEKS_EFS_CSI_DriverRole`**

### 신뢰 정책에 서비스 계정 제한 조건 추가
신뢰 관계(Trust relationships) 탭 → 편집 → `Condition`에 아래 확인/추가:
```json
"StringEquals": {
  "oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901:sub": "system:serviceaccount:kube-system:efs-csi-controller-sa",
  "oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901:aud": "sts.amazonaws.com"
}
```

### EKS Pod Identity 추가 대응 (신규 발견)
EFS CSI 드라이버 애드온 설치 화면에서 IRSA가 아니라 **EKS Pod Identity**(더 최신 방식, `pods.eks.amazonaws.com` 서비스 프린시펄을 신뢰) 방식으로 역할을 요구하는 것을 확인. IRSA용으로 만든 역할은 그대로 쓸 수 없어(신뢰 정책이 다름), **같은 역할에 신뢰 문장을 하나 더 추가**해서 IRSA와 Pod Identity 양쪽에서 동시에 사용 가능하도록 처리:

`AmazonEKS_EFS_CSI_DriverRole`의 신뢰 관계에 아래 문장을 **추가**(기존 OIDC 문장은 유지):
```json
{
  "Effect": "Allow",
  "Principal": { "Service": "pods.eks.amazonaws.com" },
  "Action": ["sts:AssumeRole", "sts:TagSession"]
}
```

최종 신뢰 정책 구조:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "arn:aws:iam::<계정ID>:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901:sub": "system:serviceaccount:kube-system:efs-csi-controller-sa",
          "oidc.eks.ap-northeast-2.amazonaws.com/id/93A439E84E8E23F9A1D97A8E222A5901:aud": "sts.amazonaws.com"
        }
      }
    },
    {
      "Effect": "Allow",
      "Principal": { "Service": "pods.eks.amazonaws.com" },
      "Action": ["sts:AssumeRole", "sts:TagSession"]
    }
  ]
}
```

---

## 7단계 — EFS CSI 드라이버 애드온 설치

EKS 콘솔 → 클러스터 → **애드온(Add-ons)** 탭 → **추가 애드온 받기**
1. `Amazon EFS CSI Driver` 검색 → 체크 → 다음
2. 애드온 설정 구성 화면에서 **Pod Identity 연결**이 2개 표시됨:
   - **서비스 계정의 Pod Identity IAM 역할: `efs-csi-controller-sa`** → `AmazonEKS_EFS_CSI_DriverRole` 선택
   - **서비스 계정의 Pod Identity IAM 역할: `efs-csi-node-sa`** → `AmazonEKS_EFS_CSI_DriverRole` 선택 (동일 역할 재사용 — 두 컴포넌트 다 EFS API 권한만 필요해서 하나의 역할 공유해도 무방)
3. 생성

### 확인
- 애드온 목록에서 상태 `Active` 확인
- (kubectl 있다면):
  ```bash
  kubectl get pods -n kube-system | grep efs-csi
  ```
  `efs-csi-controller-*`, `efs-csi-node-*` Pod가 `Running` 상태인지 확인

---

## 8단계 — RDS(Postgres) 생성

### 데이터베이스 생성
RDS 콘솔 → 데이터베이스 → **데이터베이스 생성**

| 항목 | 값 |
|---|---|
| 데이터베이스 생성 방식 | 표준 생성(Standard create) |
| 엔진 유형 | PostgreSQL |
| 템플릿 | 프리 티어(Free tier) — 인스턴스/스토리지/Multi-AZ가 프리티어에 맞게 자동 설정됨 |
| DB 인스턴스 식별자 | `conservation-db` |
| 마스터 사용자 이름 | `conservation` (기존 `docker-compose.yml`의 `POSTGRES_USER`와 동일) |
| 마스터 암호 | 기존 `.env`의 `POSTGRES_PASSWORD`와 동일하게 지정 |
| 인스턴스 클래스 | `db.t3.micro`(또는 `db.t4g.micro`, 프리티어 템플릿이 자동 선택) |
| 스토리지 | 기본값 20GB |
| VPC | EKS 클러스터와 동일 (`vpc-014228e3218853231`) |
| 퍼블릭 액세스 | 아니오(No) — EKS Pod에서만 접근 |
| VPC 보안 그룹 | 새로 생성 (`conservation-db-sg`) |
| 초기 데이터베이스 이름 | `conservation` (기존 `POSTGRES_DB`와 동일) |

생성에 5~10분 소요.

### 보안 그룹 — Postgres 포트(5432) 허용
EFS 때와 동일한 패턴:
1. DB 상세 화면 → **연결 및 보안** 탭 → 보안 그룹(`conservation-db-sg`) 링크 클릭 → EC2 콘솔로 이동
2. **인바운드 규칙 편집** → 규칙 추가: 유형 `PostgreSQL`, 포트 `5432`(자동), 소스 = **EKS 워커 노드의 보안 그룹**

### 엔드포인트
생성 완료 후 DB 상세 화면의 **엔드포인트(Endpoint)** 값을 나중에 K8s Secret의 `SPRING_DATASOURCE_URL`에 사용 (`conservation-db.xxxxxxxxxx.ap-northeast-2.rds.amazonaws.com` 형태).

### 상태
✅ 생성 완료 (사용자 확인, 정확한 엔드포인트 값/보안 그룹 인바운드 설정 완료 여부는 추후 기록 필요)

---

## 9단계 — kubectl 연동 + EFS StorageClass/PV/PVC 생성

### kubectl 로컬 연동
`kubectl apply`는 EKS 클러스터 내부가 아니라 **로컬 컴퓨터에서 실행**한다. kubectl은 로컬 CLI 도구이며, kubeconfig에 등록된 자격증명으로 EKS API 서버에 원격 요청을 보내는 방식으로 동작한다.

```bash
kubectl version --client
aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2
kubectl get nodes
```

### 트러블슈팅 — IAM 사용자에 EKS 권한 부재
`conservation-cloud-deployer`(1단계에서 ECR push용으로만 권한을 준 IAM 사용자)로 `aws eks update-kubeconfig` 실행 시 아래 에러 발생:
```
AccessDeniedException: User: arn:aws:iam::815373273907:user/conservation-cloud-deployer is not authorized to perform: eks:DescribeCluster on resource: arn:aws:eks:ap-northeast-2:815373273907:cluster/conservation-cluster because no identity-based policy allows the eks:DescribeCluster action
```

**원인**: 권한이 두 층으로 나뉘어 있음:
1. **IAM 권한**: `eks:DescribeCluster` 등 EKS API 호출 권한 (지금 막힌 지점)
2. **K8s 자체 RBAC 권한**: IAM 권한이 있어도 클러스터 내부적으로 "이 IAM 사용자가 뭘 할 수 있는지"를 EKS Access Entry로 별도 허용해야 함 (다음에 막힐 수 있는 지점)

**해결 1 — IAM 인라인 정책 추가**:
IAM 콘솔 → 사용자 → `conservation-cloud-deployer` → 권한 탭 → 인라인 정책 생성 → 이름 `EKSDescribeAccess`:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "eks:DescribeCluster",
                "eks:ListClusters"
            ],
            "Resource": "*"
        }
    ]
}
```

**해결 2 — EKS Access Entry로 K8s RBAC 권한 부여**:
EKS 콘솔 → 클러스터 → **액세스(Access)** 탭 → IAM 액세스 항목 → 생성
| 항목 | 값 |
|---|---|
| IAM 주체(Principal) | `conservation-cloud-deployer` |
| 액세스 정책(Access policy) | `AmazonEKSClusterAdminPolicy` |

**참고**: 정책을 정확히 붙였는데도 한 번에 안 되고 `AccessDeniedException`이 반복됐음 — **IAM 정책 전파에 약간의 지연이 있었던 것으로 추정**, 몇 분 후 재시도하니 정상 동작함.

### EFS StorageClass / PersistentVolume / PersistentVolumeClaim

`k8s/storage.yaml` (레포 루트) 작성:
```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-sc
provisioner: efs.csi.aws.com
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: shared-efs-pv
spec:
  capacity:
    storage: 5Gi
  volumeMode: Filesystem
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: efs-sc
  csi:
    driver: efs.csi.aws.com
    volumeHandle: fs-0edcc867a8c48ef4c
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: shared-efs-pvc
  namespace: default
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-sc
  resources:
    requests:
      storage: 5Gi
```

**설명**:
- `StorageClass(efs-sc)`: EFS 드라이버로 스토리지를 만들겠다는 선언 (정적 프로비저닝 방식이라 이름표 역할)
- `PersistentVolume(shared-efs-pv)`: `volumeHandle`에 EFS 파일 시스템 ID(`fs-0edcc867a8c48ef4c`)를 직접 연결. `accessModes: ReadWriteMany`로 여러 Pod 동시 마운트 가능. `storage: 5Gi`는 EFS가 실제로 용량 제한이 없어 형식적인 값(동작에 영향 없음).
- `PersistentVolumeClaim(shared-efs-pvc)`: 이 PVC 이름을 **`conservation-backend`와 `xray-ai` 두 Deployment 양쪽의 `volumes` 섹션에서 동일하게 참조**하면, 서로 다른 Pod가 같은 EFS를 마운트하게 됨 — 로컬의 `./shared` bind mount를 클라우드에서 재현하는 핵심.

### 적용 결과
```bash
kubectl apply -f k8s/storage.yaml
kubectl get pv,pvc
```
```
NAME                             CAPACITY   ACCESS MODES   RECLAIM POLICY   STATUS   CLAIM                    STORAGECLASS   AGE
persistentvolume/shared-efs-pv   5Gi        RWX            Retain           Bound    default/shared-efs-pvc   efs-sc         8s

NAME                                   STATUS   VOLUME          CAPACITY   ACCESS MODES   STORAGECLASS   AGE
persistentvolumeclaim/shared-efs-pvc   Bound    shared-efs-pv   5Gi        RWX            efs-sc         8s
```
✅ `shared-efs-pvc`가 `Bound` 상태로 정상 생성 확인됨.

---

## 10단계 — Secret 생성 (`app-secrets`)

### 방침: 민감값은 채팅/Git에 절대 올리지 않고 로컬에서 직접 주입
`kubectl create secret`의 `--from-env-file` 옵션으로, 로컬 `.env` 파일의 `KEY=value` 줄을 그대로 K8s Secret으로 생성한다. 값이 대화 내용이나 Git에 전혀 노출되지 않는다.

### 1) K8s 전용 env 파일 준비
기존 `.env`는 docker-compose의 `postgres` 컨테이너 이름으로 접속하는 값이 들어있는데, K8s에서는 **RDS 엔드포인트**로 접속해야 해서 달라짐. 레포 루트에 `.env.k8s` 신규 생성:
```env
OPENAI_API_KEY=<기존 .env 값>
POSTGRES_PASSWORD=<기존 .env 값>
AWS_ACCESS_KEY_ID=<기존 .env 값>
AWS_SECRET_ACCESS_KEY=<기존 .env 값>
AWS_S3_BUCKET=<기존 .env 값>
AWS_REGION=ap-northeast-2

SPRING_DATASOURCE_URL=jdbc:postgresql://conservation-db.cx8yims0kr3g.ap-northeast-2.rds.amazonaws.com:5432/conservation
SPRING_DATASOURCE_USERNAME=conservation
DATABASE_URL=postgresql://conservation:<POSTGRES_PASSWORD 실제값>@conservation-db.cx8yims0kr3g.ap-northeast-2.rds.amazonaws.com:5432/conservation
```

### 2) `.gitignore`에 추가
`.gitignore`에 `/.env`는 이미 있었지만 `.env.k8s`는 신규 파일이라 규칙에 없었음 → `/.env.k8s` 줄 추가함 (git status에서 추적 안 되는 것 확인 완료).

### 3) Secret 생성
```bash
kubectl create secret generic app-secrets --from-env-file=.env.k8s
```

### 4) 검증 (값 노출 없이 키 목록만 확인)
```bash
kubectl get secret app-secrets -o jsonpath='{.data}' | tr ',' '\n' | grep -o '"[A-Z_]*"' | sort -u
```
결과: `AWS_ACCESS_KEY_ID`, `AWS_REGION`, `AWS_SECRET_ACCESS_KEY`, `DATABASE_URL`, `OPENAI_API_KEY`, `POSTGRES_PASSWORD`, `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME` — 8개 키 전부 정상 확인됨. ✅

### 5) Deployment에서 사용법
```yaml
envFrom:
  - secretRef:
      name: app-secrets
```
이렇게 참조하면 Secret 안의 모든 키가 컨테이너 환경변수로 자동 주입됨 (docker-compose의 `environment:` 섹션과 동일 효과).

---

## 11단계 — 애플리케이션 Deployment/Service 매니페스트 작성 (`k8s/app.yaml`)

### 사전 확인: `conservation-guide-ai`의 RAG 문서/인덱스는 이미지에 baked-in
`Dockerfile`이 `COPY app/ ./app/`로 되어 있어 `cleaning_rag`/`bonding_rag`/`reinforcement_rag`의 documents/index 폴더가 **이미 이미지 빌드 시점에 포함**되어 있음. docker-compose의 관련 볼륨 마운트는 로컬 개발 편의용(재빌드 없이 문서 수정)일 뿐이라, K8s에서는 이 부분을 따로 마운트할 필요 없음.

### 전체 내용
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
              value: "/shared/jobs"
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
              value: "/shared/cache"
          envFrom:
            - secretRef:
                name: app-secrets
          volumeMounts:
            - name: shared-storage
              mountPath: /shared
      volumes:
        - name: shared-storage
          persistentVolumeClaim:
            claimName: shared-efs-pvc
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
            - name: XRAY_AI_BASE_URL
              value: "http://xray-ai:8000"
            - name: XRAY_AI_TIMEOUT_SECONDS
              value: "300"
            - name: XRAY_AI_CONFIG_NAME
              value: "config.batch_fast.color_slot_voronoi_all_fragments_v13_conservative.json"
            - name: XRAY_STORAGE_LOCAL_ROOT
              value: "/shared/jobs"
            - name: XRAY_STORAGE_CONTAINER_ROOT
              value: "/shared/jobs"
            - name: SPRING_DATASOURCE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: POSTGRES_PASSWORD
          envFrom:
            - secretRef:
                name: app-secrets
          volumeMounts:
            - name: shared-storage
              mountPath: /shared
      volumes:
        - name: shared-storage
          persistentVolumeClaim:
            claimName: shared-efs-pvc
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
```

### 설계 포인트
| 서비스 | 이미지 | 포트 | 비고 |
|---|---|---|---|
| `conservation-guide-ai` | ECR `conservation-guide-ai:latest` | 8000 | `app-secrets` 전체를 `envFrom`으로 주입 |
| `xray-ai` | ECR `xray-ai:latest` | 8000 | docker-compose의 `XRAY_*` 값 그대로 + `/shared` EFS 마운트 |
| `conservation-backend` | ECR `conservation-backend:latest` | 8080 | 다른 두 서비스를 **K8s Service DNS 이름**(`http://conservation-guide-ai:8000`, `http://xray-ai:8000`)으로 호출 + `/shared` EFS 마운트 |

1. **`envFrom: secretRef`**: Secret 안의 키 이름이 그대로 환경변수 이름이 됨. docker-compose의 `environment: OPENAI_API_KEY: ${OPENAI_API_KEY}`와 같은 효과.
2. **`SPRING_DATASOURCE_PASSWORD`만 개별 처리한 이유**: Secret에는 `POSTGRES_PASSWORD` 키로 저장했지만 Spring이 읽는 환경변수 이름은 `SPRING_DATASOURCE_PASSWORD`라서, 키 이름을 변환해주는 개별 `valueFrom.secretKeyRef`를 추가함.
3. **서비스 간 통신 주소**: docker-compose에서 컨테이너 이름으로 서로 호출했던 것처럼, K8s에서도 **Service 이름이 곧 DNS 이름**이라 거의 동일하게 동작 (`http://conservation-guide-ai:8000` 형태로 클러스터 내부에서 자동 resolve).
4. **외부 노출은 아직 안 함**: 3개 다 `type: ClusterIP`로 클러스터 내부에서만 접근 가능. 외부 노출은 다음 단계(ALB Ingress 또는 LoadBalancer Service) 과제.

### 적용
```bash
kubectl apply -f k8s/app.yaml
kubectl get pods
```

### 외부에서 API에 접속하는 방법 (아직 ClusterIP라 별도 조치 필요)

**옵션 A — `kubectl port-forward` (즉시 테스트용)**
```bash
kubectl port-forward svc/conservation-backend 8080:8080
```
로컬 포트를 클러스터 안 Service로 터널링. 이 터미널을 유지하는 동안 로컬에서 `http://localhost:8080`으로 접속 가능. 터미널 닫으면 끊김, 본인 컴퓨터에서만 접속 가능 — 임시 테스트 전용.

**옵션 B — Service를 `type: LoadBalancer`로 변경 (실제 외부 URL 필요 시)**
```yaml
spec:
  type: LoadBalancer   # ClusterIP → LoadBalancer
```
ALB Ingress Controller(Helm 필요) 설치 없이도, AWS가 자동으로 로드밸런서(ELB)를 만들어 `EXTERNAL-IP`를 부여함. `kubectl get svc conservation-backend`로 확인. 도메인/HTTPS/경로 라우팅 같은 고급 기능이 필요해지면 그때 ALB Ingress로 업그레이드.

### 배포 결과 및 진행 중인 트러블슈팅

`kubectl apply -f k8s/app.yaml` 직후 Pod 상태:
```
NAME                                     READY   STATUS              RESTARTS      AGE
conservation-backend-6b9d8f595f-h7fsb    0/1     ContainerCreating   0             6m11s
conservation-guide-ai-78857667f5-gm8kk   1/1     Running             5 (106s ago)   6m11s
xray-ai-5b9fbfb767-qglkr                 0/1     ContainerCreating   0             6m11s
```

**증상 1 — `conservation-guide-ai`가 계속 재시작(`Error` → 재실행 반복)**

`kubectl logs conservation-guide-ai-<pod> --previous`(직전 크래시 로그 확인)로 원인 파악:
```
File "/code/app/graph.py", line 229, in build_graph
    checkpointer.setup()
  ...
  File "/usr/local/lib/python3.12/site-packages/psycopg_pool/pool.py", line 218, in getconn
    raise PoolTimeout(
psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec
```
→ LangGraph의 Postgres 체크포인터가 RDS 연결을 30초 동안 못 맺어서 앱 자체가 시작 실패. **인증 실패(비밀번호 오류)라면 몇 초 안에 바로 에러가 나야 하는데 30초 타임아웃까지 간 것으로 보아, 인증이 아니라 네트워크 단에서 RDS에 아예 도달하지 못하는 것으로 추정.**

**유력 원인**: 8단계에서 안내했던 RDS 보안 그룹의 "PostgreSQL(5432) 인바운드, 소스 = EKS 워커 노드 보안 그룹" 규칙이 **실제로 저장되지 않았을 가능성.** (진행 중, 확인/조치 필요)

**증상 2 — `conservation-backend`, `xray-ai`가 6분 넘게 `ContainerCreating`에 멈춤**

공통점: 둘 다 `shared-efs-pvc`(EFS)를 마운트함. 원인 진단용:
```bash
kubectl describe pod conservation-backend-<pod>
```
`Events` 섹션에서 `MountVolume.SetUp failed`/`Unable to mount volumes` 여부 확인 필요 — EFS 마운트 대상 보안 그룹의 NFS(2049) 인바운드 소스가 실제 워커 노드 보안 그룹과 정확히 일치하는지 재확인 필요. **(진행 중, 결과 미확인)**

---

## 현재까지 진행 상황 요약

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | ECR 리포지토리 3개 생성 + 이미지 push (`conservation-backend`, `conservation-guide-ai`, `xray-ai`) | 완료 |
| 2 | IAM 역할 생성 (`eksClusterRole`, `eksNodeRole`) | 완료 |
| 3 | EKS 클러스터(`conservation-cluster`) + 노드 그룹(`workers`, c7i-flex.large x2 — 프리티어 제약으로 t3.medium에서 변경) 생성 | 완료 |
| 4 | EFS 파일 시스템(`conservation-shared`) + 마운트 대상 4개 + 보안 그룹(NFS 2049) | 완료 |
| 5 | OIDC 자격 증명 공급자 연동 | 완료 |
| 6 | EFS CSI 드라이버용 IAM 역할(`AmazonEKS_EFS_CSI_DriverRole`, IRSA+Pod Identity 겸용) | 완료 |
| 7 | EFS CSI 드라이버 애드온 설치 (Pod Identity: `efs-csi-controller-sa`, `efs-csi-node-sa`) | 완료 |
| 8 | RDS(Postgres, `conservation-db`) 생성. 엔드포인트: `conservation-db.cx8yims0kr3g.ap-northeast-2.rds.amazonaws.com` | 완료 |
| 9 | kubectl 로컬 연동 (IAM `EKSDescribeAccess` 정책 + EKS Access Entry `AmazonEKSClusterAdminPolicy`) + `k8s/storage.yaml`(StorageClass/PV/PVC) 적용, `shared-efs-pvc` Bound 확인 | 완료 |

## 남은 작업 (미착수)

1. **애플리케이션 K8s 매니페스트 작성**: 3개 서비스(`conservation-backend`, `conservation-guide-ai`, `xray-ai`) 각각의 Deployment + Service + Secret/ConfigMap
   - Secret으로 관리해야 할 값: `OPENAI_API_KEY`, `POSTGRES_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, RDS 엔드포인트 기반 `SPRING_DATASOURCE_URL` 등
   - `shared-efs-pvc`를 `conservation-backend`, `xray-ai` 두 Deployment의 `volumes`에서 동일하게 참조해서 `/shared`에 마운트
2. **ALB Ingress Controller 설치**: `conservation-backend`(관문 역할)만 외부 노출 — 콘솔에 설치 버튼이 없어 Helm 필요 (유일하게 순수 콘솔 GUI로 안 되는 지점으로 논의됨)
3. **IRSA로 S3 접근 전환 검토**: 지금 `.env`의 정적 `AWS_ACCESS_KEY_ID`/`SECRET` 대신, `conservation-backend`의 ServiceAccount에 S3 접근 역할을 직접 연결
4. **CI/CD 파이프라인**: GitHub Actions(OIDC 기반 IAM 역할 연동, ECR push, `kubectl set image` 배포) 또는 AWS 콘솔 네이티브 CodePipeline + CodeBuild 중 선택
