# AWS ECR + EKS 배포 가이드 (처음부터, 4개 서비스 전체)

이 문서는 AWS 계정에 **아무것도 없는 상태**를 가정하고, `conservation-backend`(Spring) + `conservation-guide-ai`(FastAPI) + `xray-ai`(FastAPI) + `pottery-inspection-ai`(FastAPI) + Postgres(RDS)를 EKS에 올리는 전체 과정을 처음부터 끝까지 순서대로 정리한 것이다. AWS Console(GUI) 기준으로 쓰고, CLI가 필요한 지점(`aws`, `kubectl`, `docker`)만 명령어로 표기한다.

계정ID/리전은 이 프로젝트에서 실제로 쓰는 `815373273907` / `ap-northeast-2`(서울)로 통일해서 표기한다. 다른 계정에서 따라 할 경우 이 값만 자신의 것으로 바꾸면 된다. VPC ID·서브넷 ID·EFS 파일시스템 ID·RDS 엔드포인트처럼 **자원을 새로 만들 때마다 값이 달라지는 것**은 실제 콘솔에서 나온 값으로 바꿔 써야 하므로 `<...>` 형태로 표기했다.

---

## 0단계 — 아키텍처 개요 및 결정 사항

### 구성 요소
| 구성 요소 | 역할 | 포트 | 외부 노출 |
|---|---|---|---|
| `conservation-backend` | Spring, 모든 요청의 관문 | 8080 | ✅ 유일하게 외부 노출 |
| `conservation-guide-ai` | FastAPI, LangGraph 기반 보존처리 가이드 AI | 8000 | ❌ 클러스터 내부 전용 |
| `xray-ai` | FastAPI, X-RAY 조각 결합/이상영역 탐지 | 8000 | ❌ 클러스터 내부 전용 |
| `pottery-inspection-ai` | FastAPI, 도자기 육안조사 AI(형태/유약/시대 판정) | 8000 | ❌ 클러스터 내부 전용 |
| `postgres` (RDS) | LangGraph 체크포인터 + 앱 DB | 5432 | ❌ EKS Pod 전용 |

로컬 `docker-compose.yml` 기준 4개 서비스 이미지를 ECR에 올리고, EKS 클러스터에서 각각 별도 Deployment로 띄우는 것이 목표.

### 사전에 확정한 아키텍처 결정 (이유 포함)

- **서비스마다 별도 Deployment로 유지, 하나의 Pod로 합치지 않는다.**
  이유: 리소스 프로파일이 다름(`xray-ai`/`pottery-inspection-ai`는 torch 기반이라 무거움, `backend`는 가벼움), 독립적 스케일링·배포(하나만 이미지 업데이트해도 나머지 무중단) 필요.
- **`conservation-backend` ↔ `xray-ai`가 공유하는 `shared/` 폴더는 EFS(ReadWriteMany)로 재현한다.**
  이유: EBS는 `ReadWriteOnce`라 Pod 하나만 마운트 가능해서 두 서비스가 동시에 못 씀.
- **Postgres는 RDS로 둔다.**
  이유: EBS PVC 방식은 IAM 역할/Pod Identity 설정을 EFS 때와 별개로 한 번 더 거쳐야 해서 번거로움. RDS `db.t3.micro` 단일 AZ는 프리티어로 12개월 무료라 비용 차이도 없음.
- **`pottery-inspection-ai`의 `ai_hub`(시대 판정 CNN 모델)는 `Dockerfile`에 `COPY`로 이미지에 직접 포함시킨다.**
  이유: 처음엔 "git에도 이미지에도 없는 대용량 파일"이라고 보고 S3 업로드 + initContainer 다운로드 방식을 세웠었는데, 실습 중 실제로는 `best_model.pt`(43MB)가 **git에 이미 커밋되어 있다는 걸 확인**했다(`docker-compose.yml`의 "용량 때문에 git에 안 올라가 있음" 주석이 오래돼서 틀린 상태였음). 이미 저장소 체크아웃에 존재하는 파일이라 굳이 S3·initContainer·추가 IAM 권한까지 얹을 이유가 없어져서, `.dockerignore` 예외 처리 + `Dockerfile` `COPY` 한 줄로 단순화했다. (자세한 경위는 11단계)
- **Hugging Face 가중치 캐시(`pottery_hf_cache`)는 `emptyDir`로 대체한다.**
  이유: 원본이 Hugging Face Hub에 항상 있는 다운로드 캐시일 뿐이라, 없어져도 다시 받으면 그만 — EFS/EBS까지 붙일 정도로 중요한 데이터가 아님.

---

## 1단계 — 사전 준비

- AWS 계정, 콘솔 로그인 가능한 계정(루트 또는 개인 IAM 사용자)
- 로컬에 설치: [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [kubectl](https://kubernetes.io/docs/tasks/tools/), Docker
- 리전은 `ap-northeast-2`(서울)로 통일

### CLI 전용 IAM 사용자 만들기
CLI(`aws`, `kubectl`)에서 쓸 별도 IAM 사용자를 하나 만든다. 이번 작업은 IAM 역할 생성/EKS/RDS/EFS/S3처럼 권한 범위가 넓어서, 세세하게 권한을 하나씩 골라 붙이기보다 관리자 권한을 가진 사용자 하나로 진행하는 게 실용적이다.

IAM 콘솔 → 사용자 → **사용자 생성**
1. 사용자 이름: `conservation-cloud-deployer`(예시, 자유롭게)
2. 권한 옵션: "직접 정책 연결" → **`AdministratorAccess`**
3. 생성 후 해당 사용자 → **보안 자격 증명** 탭 → **액세스 키 만들기** (사용 사례: 명령줄 인터페이스)

> **주의(9단계에서 실제로 겪음)**: 이 IAM 사용자에게 `AdministratorAccess`가 있어도, **EKS 클러스터를 콘솔(브라우저)에서 만들 때 로그인했던 계정과 이 IAM 사용자가 다르면** `kubectl`이 인증 에러를 낸다. `AdministratorAccess`는 "AWS API를 뭐든 호출할 수 있다"는 뜻이지 "쿠버네티스 클러스터 내부에서 뭘 할 수 있는지"는 EKS가 별도로 관리하는 목록(Access Entry)이라 완전히 다른 얘기다. 자세한 대응은 9단계 참고.

```bash
aws configure
# AWS Access Key ID / Secret Access Key / Default region name(ap-northeast-2) / output format(json)
```

---

## 2단계 — ECR (Elastic Container Registry): 리포지토리 생성 + 이미지 push

### 리포지토리 생성 (4개)
ECR 콘솔 → Repositories → **Create repository**, 아래 설정으로 4번 반복:

| 항목 | 값 |
|---|---|
| Visibility | Private |
| Repository name | `conservation-backend` / `conservation-guide-ai` / `xray-ai` / `pottery-inspection-ai` |
| Tag immutability | Mutable (기본값) |
| Scan on push | Enable |
| Encryption | AES-256 (기본값) |

### 필요한 IAM 권한 (push용)
push를 실행하는 IAM 사용자에게 **`AmazonEC2ContainerRegistryPowerUser`** 관리형 정책 부여. (정책 이름에 `EC2Container`가 들어있지만 실제 EC2 인스턴스 권한과는 무관 — ECR의 역사적 이름 잔재.)

### 이미지 빌드 컨텍스트 (서비스별 Dockerfile 위치)
**`docker build -t <repo-name> .`의 `.`(빌드 컨텍스트)는 반드시 서비스별 Dockerfile이 있는 디렉터리에서 실행**해야 한다. 한 디렉터리에서 4번 다 실행하면 잘못된 이미지가 올라간다.

| 서비스 | Dockerfile 위치 |
|---|---|
| `conservation-backend` | 레포 루트 `/Dockerfile` |
| `conservation-guide-ai` | `/ai-services/conservation-guide-ai/Dockerfile` |
| `xray-ai` | `/ai-services/xray-ai/Dockerfile` |
| `pottery-inspection-ai` | `/ai-services/pottery-inspection-ai/Dockerfile` |

### 푸시 명령
각 리포지토리 상세 화면 → **View push commands**에 계정ID/리전이 채워진 채로 표시됨. 예시(`conservation-backend`):
```bash
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com

cd conservation_backend   # 레포 루트
docker build -t conservation-backend .
docker tag conservation-backend:latest 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
docker push 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/conservation-backend:latest
```
나머지 3개도 동일 패턴으로, 위 표의 디렉터리에서 각각 반복한다. `pottery-inspection-ai`는 `docs/DOCKER.md`에 "torch/transformers/SAM2가 들어가서 이미지가 큽니다(수 GB)"라고 명시되어 있으니 다른 서비스보다 push/pull 시간이 오래 걸릴 수 있다는 걸 감안한다.

### 검증
ECR 콘솔에서 각 리포지토리의 **Images** 탭 → 이미지 크기(MB) 확인. 4개 리포지토리 모두 이미지가 하나씩(태그 `latest`) 올라와 있으면 정상. (참고: Images 탭에 3줄씩 뜨는 건 정상 동작 — Image Index(포인터) + Image(실제 레이어) + Image 0.00MB(provenance/SBOM 메타데이터)로, 이미지가 3개 올라간 게 아니라 논리적으로 1개다.)

> **참고(실습 중 실제로 겪음)**: `pottery-inspection-ai`처럼 torch/transformers가 들어간 무거운 이미지를 `docker build`할 때, Windows + WSL2 환경에서 **IntelliJ 등 다른 프로그램이 갑자기 죽는 증상**이 생길 수 있다. 원인으로 의심되는 건 `C:\Users\<사용자>\.wslconfig`가 없어서 WSL2(Docker Desktop의 내부 리눅스 VM)가 쓸 수 있는 메모리에 상한이 없는 것 — 다만 이번엔 `.wslconfig`를 실제로 손대지 않은 채 재시도만으로 빌드가 정상적으로 끝났다. 같은 증상이 반복된다면 그때 이 설정을 의심해볼 것.

---

## 3단계 — IAM 역할 준비

ECR push용 IAM 사용자/역할은 **"사람(로컬 CLI)"**이 쓰는 것이고, EKS를 굴리려면 **"AWS 서비스/EC2 노드 자신"**이 다른 AWS 리소스에 접근할 별도 역할이 최소 2개 필요하다.

| 구분 | 누가 쓰나 | 용도 |
|---|---|---|
| ECR push용 역할(2단계) | 사람(개발자, CLI) | 로컬에서 `docker push` |
| **eksClusterRole** | AWS EKS 서비스 자체 | 클러스터를 대신 관리 (ENI 생성, 로드밸런서 연동 등) |
| **eksNodeRole** | EC2 워커 노드(서버) | ECR에서 이미지 pull, EKS API와 통신 |

### `eksClusterRole` 생성
IAM 콘솔 → 역할(Roles) → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `AWS 서비스`
2. 사용 사례(Use case): `EKS` 검색 → **EKS - Cluster** 선택
3. 권한 정책: **`AmazonEKSClusterPolicy`** (자동 선택됨)
4. 역할 이름: **`eksClusterRole`**

### `eksNodeRole` 생성
IAM 콘솔 → 역할 → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `AWS 서비스`
2. 사용 사례: `EC2`
3. 권한 정책 3개 모두 체크:
   - **`AmazonEKSWorkerNodePolicy`** — 노드가 EKS 컨트롤 플레인과 통신
   - **`AmazonEKS_CNI_Policy`** — 파드에 IP 할당하는 VPC CNI 네트워킹 플러그인
   - **`AmazonEC2ContainerRegistryReadOnly`** — ECR 4개 리포지토리에서 이미지 pull
4. 역할 이름: **`eksNodeRole`**

---

## 4단계 — EKS 클러스터 + 노드 그룹 생성

### 사전 확인: VPC/서브넷
EKS는 **서로 다른 가용영역(AZ) 2개 이상에 걸친 서브넷**이 필요하다. 서브넷 자체가 여러 AZ에 걸치는 게 아니라(서브넷은 반드시 하나의 AZ에만 속함), 서로 다른 AZ에 있는 서브넷을 여러 개 선택해서 그 집합이 2개 이상 AZ를 커버하게 만드는 것. 기본(default) VPC는 리전 안 AZ마다 서브넷을 자동으로 하나씩 만들어두므로 그대로 사용하면 된다. VPC 콘솔에서 기본 VPC의 ID(`<VPC ID>`)를 확인해둔다.

### 클러스터 생성
EKS 콘솔 → 클러스터(Clusters) → **클러스터 생성**
- 상단 모드 토글: **`Custom configuration`** 선택 (Auto Mode는 AWS가 노드그룹/스토리지/로드밸런싱을 자동 관리하는 방식이라, 직접 `eksNodeRole`을 쓰는 이번 방식과 안 맞음)

**Configure cluster**
| 항목 | 값 |
|---|---|
| Name | `conservation-cluster` |
| Kubernetes version | 기본 최신 |
| Cluster service role | `eksClusterRole` |

**Specify networking**
| 항목 | 값 |
|---|---|
| VPC | 기본 VPC (`<VPC ID>`) |
| Subnets | 최소 2개 AZ 이상 (가능하면 해당 리전의 AZ 서브넷 전부 선택) |
| Cluster endpoint access | `Public` |

**Select add-ons**: 기본값(VPC CNI, CoreDNS, kube-proxy) 유지 → **Review and create** → Create. 생성에 10~15분 소요.

> 생성 중 "클러스터 역할에 권장 관리형 정책이 없음(`AmazonEKSBlockStoragePolicyV2` 등)" 경고가 뜰 수 있는데, 이건 **EKS 자율 모드(Auto Mode)를 쓸 경우에만** 필요한 조건부 안내다. `Custom configuration`을 선택했으므로 무시해도 된다.

### 노드 그룹 추가
클러스터 상태가 `Active`가 된 후: 클러스터 상세 → **컴퓨팅(Compute)** 탭 → **노드 그룹 추가**

| 항목 | 값 |
|---|---|
| Name | `workers` |
| Node IAM role | `eksNodeRole` |
| AMI type | Amazon Linux 2 (x86) |
| Instance type | `t3.medium` (아래 참고) |
| Disk size | 기본값(20GB) |
| Desired size | 2 |
| Minimum size | 1 |
| Maximum size | 4 |
| Subnets | 클러스터와 동일 |

> **프리티어 계정 주의**: `t3.medium`으로 노드 그룹 생성 시 `AsgInstanceLaunchFailures`(`InvalidParameterCombination - not eligible for Free Tier`)로 **생성 자체가 실패할 수 있다.** 이 경우 실패한 노드 그룹을 삭제하고, 나머지 설정은 동일하게 유지한 채 Instance type만 `c7i-flex.large`(스펙은 더 높지만 이 계정 조건에서는 프리티어로 허용됨)로 바꿔 재생성한다. 프리티어로 허용되는 타입은 계정/리전마다 달라서, 실패하면 다른 타입으로 시도해보며 확인해야 한다. 이 단계에서 노드가 하나도 없으면 이후 EFS CSI 드라이버 애드온도 `저하됨(Degraded)`으로 뜬다(IAM 문제가 아니라 스케줄링할 노드가 없어서 나는 증상이니 헷갈리지 말 것).

### 완료 확인
```bash
aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2
kubectl get nodes
```
노드 그룹 상태 `Active`, `kubectl get nodes`에 `Ready` 상태 EC2 인스턴스 2대가 보이면 완료.

---

## 5단계 — EFS(Elastic File System) 생성 — `shared/` 재현

### 로컬 `shared/`가 하는 일
로컬 `docker-compose.yml`에서 `conservation-backend`와 `xray-ai` 양쪽에 `./shared:/shared`가 bind mount되어 있다. Spring이 업로드 파일을 `/shared/jobs/{jobId}/`에 저장하면 `xray-ai` 컨테이너도 같은 호스트 디스크를 보고 있어서 경로만 넘겨도 파일을 바로 찾는다. EKS에서는 두 Pod가 서로 다른 EC2 노드에 뜰 수 있어 로컬 디스크 공유가 불가능 → EFS로 네트워크 공유 스토리지를 재현해야 한다.

### 파일 시스템 생성
EFS 콘솔 → **파일 시스템 생성**
| 항목 | 값 |
|---|---|
| Name | `conservation-shared` |
| VPC | EKS 클러스터와 동일 (`<VPC ID>`) |
| 가용성 및 내구성 | Regional (멀티 AZ) |

생성 시 자동으로 각 서브넷(AZ)마다 마운트 대상(Mount target, 실제로는 ENI)이 생성된다.

### 보안 그룹 — NFS(2049) 포트 허용 (중요, 나중에 문제 생기는 지점)
1. EFS 콘솔 → 파일 시스템 → **네트워크(Network)** 탭 → 보안 그룹 컬럼에서 `sg-xxxxxxxx` 확인
2. EC2 콘솔 → **네트워크 및 보안 → 보안 그룹** → 해당 `sg-xxxxxxxx` 클릭
3. **인바운드 규칙 편집** → 규칙 추가: 유형 `NFS`(TCP 2049 자동) / 소스 = **`eksNodeRole`을 쓰는 워커 노드의 보안 그룹**
4. 저장

이 규칙이 정확히 안 걸려 있으면, 나중에 14단계에서 `conservation-backend`/`xray-ai` Pod가 `ContainerCreating`에 멈춰서 안 뜨는 문제로 이어진다 — 지금 확실히 해두는 게 낫다. (마운트 대상 생성 직후에는 상태가 `생성 중`, 1~2분 후 `사용 가능`으로 바뀌면 보안 그룹 값이 채워진다.)

> **주의(실습 중 실제로 겪음)**: 3번의 **소스** 입력창에 보안그룹 ID(`sg-...`)를 직접 타이핑하거나 붙여넣기 하면 "기존 IPv4 CIDR 규칙에 참조된 그룹 ID을(를) 지정할 수 없습니다" 에러가 난다. 콘솔이 그 값을 IP 대역(CIDR)으로 해석하려다 충돌하는 것 — **반드시 입력창을 클릭하고 드롭다운 자동완성에 뜨는 후보를 마우스로 클릭해서 선택**해야 보안그룹 참조로 정상 등록된다. RDS 보안 그룹(8단계)에서도 동일하게 적용.

### 코드 수정 필요 여부 — 불필요함
Spring/`xray-ai` 코드 모두 `/shared` 경로를 **환경변수**로만 참조하므로, K8s 매니페스트에서 컨테이너 마운트 경로만 `/shared`로 지정하면 코드 변경 없이 그대로 재사용된다.
- `src/main/resources/application.yaml`: `storage.local-root` / `storage.container-root`
- `ai-services/xray-ai/app/config.py`: `STITCH_JOBS_ROOT`, `XRAY_STITCH_CACHE_DIR`

---

## 6단계 — OIDC 자격 증명 공급자 연동

EFS CSI 드라이버(Pod)가 AWS API(EFS 마운트)를 호출하려면 전용 IAM 역할이 필요하고, Pod가 그 역할을 assume하려면 클러스터에 OIDC 공급자가 IAM에 등록되어 있어야 한다(IRSA 방식).

1. EKS 콘솔 → 클러스터 → **개요(Overview)** 탭 → **OpenID Connect provider URL** 확인 (`https://oidc.eks.ap-northeast-2.amazonaws.com/id/<OIDC ID>` 형태)
2. IAM 콘솔 → **ID 제공업체**(콘솔 버전에 따라 "자격 증명 공급자") → **공급자 추가**
   | 항목 | 값 |
   |---|---|
   | 공급자 유형 | `OpenID Connect` |
   | 공급자 URL | 1번에서 확인한 URL |
   | 대상(Audience) | `sts.amazonaws.com` |
3. **공급자 추가** 클릭. (최신 콘솔은 "지문 가져오기" 단계 없이 AWS가 자동으로 인증서 지문을 검증/등록한다.)

---

## 7단계 — EFS CSI 드라이버용 IAM 역할 + 애드온 설치

### 역할 생성
IAM 콘솔 → 역할 → **역할 생성**
1. 신뢰할 수 있는 엔티티 유형: `웹 자격 증명(Web identity)`
2. 자격 증명 공급자: 6단계에서 등록한 OIDC 공급자 선택
3. 대상(Audience): `sts.amazonaws.com`
4. 권한 정책: **`AmazonEFSCSIDriverPolicy`**
5. 역할 이름: **`AmazonEKS_EFS_CSI_DriverRole`**

### 신뢰 정책에 서비스 계정 제한 조건 추가
신뢰 관계(Trust relationships) 탭 → 편집 → `Condition`에 추가:
```json
"StringEquals": {
  "oidc.eks.ap-northeast-2.amazonaws.com/id/<OIDC ID>:sub": "system:serviceaccount:kube-system:efs-csi-controller-sa",
  "oidc.eks.ap-northeast-2.amazonaws.com/id/<OIDC ID>:aud": "sts.amazonaws.com"
}
```

### EKS Pod Identity 방식도 함께 대응
EFS CSI 드라이버 애드온 설치 화면에서 IRSA가 아니라 **EKS Pod Identity**(`pods.eks.amazonaws.com` 서비스 프린시펄을 신뢰하는 더 최신 방식) 역할을 요구할 수 있다. 같은 역할에 신뢰 문장을 하나 더 추가해서 IRSA와 Pod Identity 양쪽에서 동시에 쓸 수 있게 한다(기존 OIDC 문장은 유지):
```json
{
  "Effect": "Allow",
  "Principal": { "Service": "pods.eks.amazonaws.com" },
  "Action": ["sts:AssumeRole", "sts:TagSession"]
}
```

### 애드온 설치
EKS 콘솔 → 클러스터 → **애드온(Add-ons)** 탭 → **추가 애드온 받기**
1. `Amazon EFS CSI Driver` 검색 → 체크 → 다음
2. Pod Identity 연결 2개(`efs-csi-controller-sa`, `efs-csi-node-sa`) 모두 `AmazonEKS_EFS_CSI_DriverRole` 선택 (같은 역할 재사용해도 무방 — 둘 다 EFS API 권한만 필요)
3. 생성

### 확인
```bash
kubectl get pods -n kube-system | grep efs-csi
```
`efs-csi-controller-*`, `efs-csi-node-*` Pod가 `Running`이면 완료.

---

## 8단계 — RDS(Postgres) 생성

### 데이터베이스 생성
RDS 콘솔 → 데이터베이스 → **데이터베이스 생성**

| 항목 | 값 |
|---|---|
| 데이터베이스 생성 방식 | 표준 생성(Standard create) |
| 엔진 유형 | PostgreSQL |
| 템플릿 | 프리 티어(Free tier) |
| DB 인스턴스 식별자 | `conservation-db` |
| 마스터 사용자 이름 | `conservation` |
| 마스터 암호 | `.env`의 `POSTGRES_PASSWORD`와 동일하게 지정 |
| 인스턴스 클래스 | `db.t3.micro`(프리티어 템플릿 자동 선택) |
| 스토리지 | 기본값 20GB |
| VPC | EKS 클러스터와 동일 (`<VPC ID>`) |
| 퍼블릭 액세스 | 아니오(No) |
| VPC 보안 그룹 | 새로 생성 (`conservation-db-sg`) |
| 초기 데이터베이스 이름 | `conservation` |

생성에 5~10분 소요.

> **참고: "DB 식별자"와 "데이터베이스"는 다른 개념이다.** `conservation-db`는 **서버(인스턴스) 자체의 이름**이고, 그 서버 안에는 여러 개의 데이터베이스(스키마)가 들어갈 수 있다 — 캐비닛 하나(인스턴스, `conservation-db`)에 서랍이 여러 개(데이터베이스, `postgres`/`conservation`) 있는 구조라고 보면 된다. Postgres는 항상 기본 `postgres` 데이터베이스를 하나 갖고 있고, 위 "초기 데이터베이스 이름"에 `conservation`을 입력해야 그 이름의 데이터베이스가 추가로 생긴다. 이름이 비슷해서 헷갈리기 쉬운 지점.

### 보안 그룹 — Postgres 포트(5432) 허용 (중요, 나중에 문제 생기는 지점)
1. DB 상세 화면 → **연결 및 보안** 탭 → 보안 그룹(`conservation-db-sg`) 링크 → EC2 콘솔
2. **인바운드 규칙 편집** → 규칙 추가: 유형 `PostgreSQL`(포트 5432 자동) / 소스 = **EKS 워커 노드의 보안 그룹** (드롭다운에서 선택 — 5단계 EFS 규칙 참고, 직접 타이핑하면 에러 남)

이 규칙이 없으면 나중에 `conservation-guide-ai`가 LangGraph 체크포인터(Postgres) 연결을 30초간 못 맺고 `PoolTimeout` 에러로 계속 재시작한다 — 인증 실패라면 몇 초 안에 바로 에러가 나야 하는데 30초 타임아웃까지 가는 건 대부분 네트워크(보안 그룹) 문제다.

### 엔드포인트 기록
생성 완료 후 DB 상세 화면의 **엔드포인트(Endpoint)** 값(`conservation-db.xxxxxxxxxx.ap-northeast-2.rds.amazonaws.com` 형태)을 12단계 Secret에 쓸 것이므로 적어둔다.

### `conservation` 데이터베이스가 실제로 만들어졌는지 확인 (실습 중 실제로 겪음)
"초기 데이터베이스 이름"에 `conservation`을 입력해도, 실제로 그 데이터베이스가 안 만들어져 있는 경우가 있었다(14단계에서 `FATAL: database "conservation" does not exist` 에러로 뒤늦게 발견됨). 지금 미리 확인해두면 나중에 헤매지 않는다. RDS는 퍼블릭 액세스가 꺼져 있어 노트북에서 바로 접속이 안 되므로, 클러스터 안에 임시 psql 파드를 띄워서 확인한다(워커 노드가 아직 안 떠 있으면 14단계에서 다시 확인해도 된다):
```bash
kubectl run psql-tmp --rm -it --image=postgres:16 --restart=Never -- \
  psql "postgresql://conservation:<마스터 암호>@<엔드포인트>:5432/postgres"
```
접속되면(항상 존재하는 기본 `postgres` 데이터베이스로 접속하는 것) `\l`로 데이터베이스 목록을 보고 `conservation`이 있는지 확인한다. 없으면 그 자리에서 바로 만든다:
```sql
CREATE DATABASE conservation;
```
`\q`로 나오면 임시 파드는 `--rm` 옵션 덕분에 자동 정리된다.

---

## 9단계 — kubectl 로컬 연동

```bash
kubectl version --client
aws eks update-kubeconfig --name conservation-cluster --region ap-northeast-2
kubectl get nodes
```
`kubectl`은 자체 로그인 시스템이 없다. 요청을 보내기 직전마다 `aws eks get-token`을 내부적으로 실행해서 `aws configure`로 등록해둔 자격증명으로 짧은 시간만 유효한 토큰을 만들어 API 서버에 제출하는 방식이다.

> **주의(실습 중 실제로 겪음)**: `AdministratorAccess`가 있는 IAM 사용자로도 `kubectl get pods` 같은 명령이 아래처럼 실패할 수 있다:
> ```
> error: You must be logged in to the server (the server has asked for the client to provide credentials)
> ```
> `AccessDeniedException: ... eks:DescribeCluster`(IAM 권한 부족)와는 다른 에러다 — 이건 **AWS까지는 인증이 됐는데, 클러스터 내부에서 이 사용자를 아예 모르는 상태**라는 뜻. 원인은 권한이 두 층으로 나뉘어 있기 때문:
> 1. **IAM 권한**: "AWS API를 호출할 수 있는지" — `AdministratorAccess`면 충분.
> 2. **K8s 자체 RBAC 권한(EKS Access Entry)**: "클러스터 안에서 이 사용자가 뭘 할 수 있는지" — IAM 권한과 완전히 별개의 목록이라, `AdministratorAccess`로도 채워지지 않는다.
>
> 클러스터를 콘솔(브라우저)에서 만들면 "클러스터를 만든 사람"(그 순간 브라우저에 로그인해 있던 신원 — 루트 계정이거나 평소 쓰던 개인 계정)에게는 EKS가 자동으로 관리자 권한을 준다. 근데 CLI용으로 **따로 만든 IAM 사용자**(1단계의 `conservation-cloud-deployer`)는 같은 AWS 계정 소속이어도 **다른 신원**이라 이 자동 권한 대상에서 빠진다. 비유하면 같은 회사(계정) 소속이어도 직원(IAM 사용자)마다 사무실 열쇠를 따로 받아야 하는 것과 같음.
>
> **해결 — EKS Access Entry 추가**: EKS 콘솔 → 클러스터 → **액세스(Access)** 탭 → **IAM 액세스 항목** → **생성**
> | 항목 | 값 |
> |---|---|
> | IAM 주체(Principal) | CLI에서 쓰는 IAM 사용자(예: `conservation-cloud-deployer`) |
> | 액세스 정책(Access policy) | `AmazonEKSClusterAdminPolicy` |
>
> 생성 후 1~2분 정도 기다렸다가(권한 전파 지연) 다시 시도한다.
>
> (`AdministratorAccess`가 없는, ECR push 전용 등 권한이 더 좁은 IAM 사용자를 쓰는 경우라면 위 Access Entry에 더해 `eks:DescribeCluster`/`eks:ListClusters` IAM 정책도 별도로 필요할 수 있다.)

---

## 10단계 — EFS StorageClass / PersistentVolume / PersistentVolumeClaim

레포 루트에 `k8s/storage.yaml` 작성:
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
    volumeHandle: <EFS 파일시스템 ID, 예: fs-0123456789abcdef0>
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
- `StorageClass(efs-sc)`: EFS 드라이버로 스토리지를 만들겠다는 선언(정적 프로비저닝이라 이름표 역할).
- `PersistentVolume(shared-efs-pv)`: `volumeHandle`에 5단계에서 만든 EFS 파일 시스템 ID를 직접 연결. `ReadWriteMany`로 여러 Pod 동시 마운트 가능. `storage: 5Gi`는 EFS가 실제로 용량 제한이 없어 형식적인 값.
- `PersistentVolumeClaim(shared-efs-pvc)`: 이 이름을 `conservation-backend`와 `xray-ai` 두 Deployment의 `volumes`에서 동일하게 참조하면 서로 다른 Pod가 같은 EFS를 마운트하게 된다.

```bash
kubectl apply -f k8s/storage.yaml
kubectl get pv,pvc
```
`shared-efs-pvc`가 `Bound` 상태로 뜨면 완료.

---

## 11단계 — `pottery-inspection-ai` 전용 준비: `ai_hub` 모델을 이미지에 포함

### 처음 계획 vs 실제로 한 것
처음엔 `ai_hub/pottery_multitask_model_v2`(시대 판정 CNN 모델, 약 43MB)가 "용량 때문에 git과 Docker 이미지 양쪽 모두에서 제외되어 있다"(`docker-compose.yml` 주석 기준)고 보고, S3 업로드 + Pod 시작 시 initContainer 다운로드 방식을 세웠었다. 실습 중 실제로 확인해보니 **그 주석이 오래돼서 틀린 상태였다** — `best_model.pt`는 이미 git에 커밋되어 있었다(LFS도 아닌 평범한 blob, `git ls-files`로 확인 가능). git에 이미 있는 파일이라면 이미지 빌드 시점에 저장소 체크아웃에 이미 존재하는 것이므로, 굳이 S3에 별도로 올려두고 Pod마다 매번 네트워크로 다시 받아올 이유가 없다 — 그래서 **`Dockerfile`에 `COPY`로 직접 굽는 방식으로 계획을 바꿨다.**

(43MB는 torch/SAM2까지 들어간 전체 이미지 크기에 비하면 미미한 수준이라, "용량이 커서 못 넣는다"는 원래 전제 자체도 이미 안 맞았다.)

### `Dockerfile` 수정
`COPY app/ ./app/` 다음 줄에 추가:
```dockerfile
# 시대 판정 CNN 모델(43MB, git에 커밋되어 있음)도 이미지에 함께 굽는다.
COPY ai_hub/pottery_multitask_model_v2 ./ai_hub/pottery_multitask_model_v2
```

### `.dockerignore` 수정
기존엔 `ai_hub/` 전체가 빌드 컨텍스트에서 제외돼 있어서, git에 파일이 있어도 이미지 빌드 시점엔 안 보였다. 디렉터리 자체를 통째로 제외하면 그 안의 특정 경로만 `!`로 다시 포함시킬 수 없기 때문에(Docker의 제약), 아래처럼 "직계 자식만 제외"로 바꾸고 필요한 하나만 다시 포함시키는 방식을 썼다:
```diff
-ai_hub/
+ai_hub/*
+!ai_hub/pottery_multitask_model_v2
```

### 결과
S3 업로드, initContainer, 추가 IAM 권한(S3 버킷 접근)이 전부 필요 없어졌다. 이미지를 다시 빌드/push하면 끝:
```bash
cd ai-services/pottery-inspection-ai
docker build -t pottery-inspection-ai .
docker tag pottery-inspection-ai:latest 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/pottery-inspection-ai:latest
docker push 815373273907.dkr.ecr.ap-northeast-2.amazonaws.com/pottery-inspection-ai:latest
```
이 프로젝트 기준 최종 이미지 크기는 CONTENT SIZE 약 849MB(`docker images pottery-inspection-ai`로 확인) — `docs/DOCKER.md`가 예고했던 "수 GB"보다는 작았다(CPU 전용 torch로 설치한 덕분으로 추정).

> 참고로 이 폴더가 IntelliJ 프로젝트 창(왼쪽 파일 트리)에서 안 보일 수 있는데, 이건 파일이 없는 게 아니라 IntelliJ가 해당 폴더를 "제외됨(Excluded)"으로 표시해서 트리에만 안 보이는 것일 수 있다 — 실제 파일 존재 여부는 탐색기나 터미널로 확인하는 게 확실하다.

---

## 12단계 — Secret 생성 (`app-secrets`)

### 방침: 민감값은 채팅/Git에 절대 올리지 않고 로컬에서 직접 주입
`kubectl create secret`의 `--from-env-file` 옵션으로 로컬 env 파일의 `KEY=value` 줄을 그대로 K8s Secret으로 만든다.

### 1) K8s 전용 env 파일 준비
기존 `.env`는 docker-compose의 `postgres` 컨테이너 이름으로 접속하는 값이 들어있는데, K8s에서는 **RDS 엔드포인트**로 접속해야 해서 달라진다. 레포 루트에 `.env.k8s` 신규 생성:
```env
OPENAI_API_KEY=<기존 .env 값>
POSTGRES_PASSWORD=<기존 .env 값>
AWS_ACCESS_KEY_ID=<기존 .env 값>
AWS_SECRET_ACCESS_KEY=<기존 .env 값>
AWS_S3_BUCKET=<S3 버킷 이름>
AWS_REGION=ap-northeast-2

SPRING_DATASOURCE_URL=jdbc:postgresql://<RDS 엔드포인트>:5432/conservation
SPRING_DATASOURCE_USERNAME=conservation
DATABASE_URL=postgresql://conservation:<POSTGRES_PASSWORD 실제값>@<RDS 엔드포인트>:5432/conservation
```

`AWS_S3_BUCKET`은 로컬 `.env`에도 원래 있어야 하는 값인데(코드가 `aws.s3.bucket: ${AWS_S3_BUCKET}`로 참조) 빠져 있기 쉬우니, 이 기회에 로컬 `.env`에도 함께 채워 넣는다.

### 2) `.gitignore`에 추가
`.gitignore`에 `/.env`는 있어도 `.env.k8s`는 신규 파일이라 규칙에 없을 수 있다. 아래 줄을 추가하고 `git status`로 추적 안 되는지 확인한다:
```
/.env.k8s
```

### 3) Secret 생성
```bash
kubectl create secret generic app-secrets --from-env-file=.env.k8s
```

### 4) 검증 (값 노출 없이 키 목록만 확인)
```bash
kubectl get secret app-secrets -o jsonpath='{.data}' | tr ',' '\n' | grep -o '"[A-Z_]*"' | sort -u
```
`AWS_ACCESS_KEY_ID`, `AWS_REGION`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`, `DATABASE_URL`, `OPENAI_API_KEY`, `POSTGRES_PASSWORD`, `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME` — 9개 키가 전부 나오면 정상.

### 5) Deployment에서 사용법
```yaml
envFrom:
  - secretRef:
      name: app-secrets
```
Secret 안의 모든 키가 컨테이너 환경변수로 자동 주입된다(docker-compose의 `environment:` 섹션과 동일 효과).

---

## 13단계 — 애플리케이션 Deployment/Service 매니페스트 작성 (`k8s/app.yaml`)

### 사전 확인: `conservation-guide-ai`의 RAG 문서/인덱스는 이미지에 baked-in
`Dockerfile`이 `COPY app/ ./app/`로 되어 있어 `cleaning_rag`/`bonding_rag`/`reinforcement_rag`의 documents/index 폴더가 이미지 빌드 시점에 이미 포함된다. docker-compose의 관련 볼륨 마운트는 로컬 개발 편의용(재빌드 없이 문서 수정)일 뿐이라 K8s에서는 마운트할 필요 없음.

레포 루트에 `k8s/app.yaml` 작성:

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
            - name: POTTERY_INSPECTION_AI_BASE_URL
              value: "http://pottery-inspection-ai:8000"
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
| `pottery-inspection-ai` | ECR `pottery-inspection-ai:latest` | 8000 | `ai_hub` 모델은 이미지에 이미 포함(11단계) — initContainer 불필요, hf 캐시만 emptyDir |
| `conservation-backend` | ECR `conservation-backend:latest` | 8080 | 나머지 세 서비스를 K8s Service DNS 이름으로 호출 + `/shared` EFS 마운트 |

1. **`envFrom: secretRef`**: Secret 안의 키 이름이 그대로 환경변수 이름이 됨.
2. **`SPRING_DATASOURCE_PASSWORD`만 개별 처리**: Secret에는 `POSTGRES_PASSWORD` 키로 저장했지만 Spring이 읽는 환경변수 이름은 `SPRING_DATASOURCE_PASSWORD`라서, 키 이름을 변환해주는 개별 `valueFrom.secretKeyRef`를 추가함.
3. **서비스 간 통신 주소**: docker-compose에서 컨테이너 이름으로 서로 호출했던 것처럼, K8s에서도 **Service 이름이 곧 DNS 이름**이라 거의 동일하게 동작(`http://xray-ai:8000` 형태로 클러스터 내부에서 자동 resolve).
4. **`pottery-inspection-ai`는 initContainer가 없다**: 11단계에서 `ai_hub` 모델을 이미지에 직접 구웠기 때문에, Pod가 뜨자마자 바로 모델을 쓸 수 있다. `hf-cache`(emptyDir)만 남아있는데, 이건 Hugging Face Hub에서 받는 별개의 가중치(Grounding DINO/SAM2)를 캐싱하는 용도라 무관.
5. **`pottery-inspection-ai`는 `/shared`(EFS)를 마운트하지 않음** — 다른 서비스와 파일을 주고받지 않는 독립 서비스라서.
6. **외부 노출은 아직 안 함**: 4개 다 `type: ClusterIP`로 클러스터 내부에서만 접근 가능. 외부 노출은 15단계.

---

## 14단계 — 적용 및 검증

```bash
kubectl apply -f k8s/app.yaml
kubectl get pods -w
```

`pottery-inspection-ai`는 `Init:0/1` → `PodInitializing` → `Running` 순으로 넘어가는지 확인한다.

### 문제가 생기면 확인할 것 (근본 원인이 대부분 이 중 하나, 실습 중 실제로 겪은 순서대로)
| 증상 | 원인 후보 | 확인 방법 |
|---|---|---|
| `conservation-guide-ai`가 계속 재시작(`Error`/`CrashLoopBackOff`) | RDS 보안 그룹 5432 인바운드 누락(8단계) — 네트워크 자체가 막힌 경우 | `kubectl logs <pod> --previous`에서 `psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec` 확인 (30초까지 가는 건 대부분 네트워크 문제) |
| `conservation-backend`가 뜨자마자 바로(몇 초 안에) `Error` | RDS 마스터 암호와 Secret의 `POSTGRES_PASSWORD`가 다름 | `kubectl logs <pod> --previous`에서 `FATAL: password authentication failed for user "conservation"` 확인 — 네트워크는 성공했고 인증만 실패했다는 뜻이라 위 항목과 구분됨 |
| 비밀번호를 다시 맞췄는데도 여전히 `Error` | `.env.k8s`만 고치고 클러스터의 `app-secrets`는 재생성 안 함 | `.env.k8s` 수정 후 반드시 `kubectl delete secret app-secrets && kubectl create secret generic app-secrets --from-env-file=.env.k8s` + `kubectl rollout restart deployment/...`까지 해야 반영됨. 로컬 파일만 고친다고 클러스터 안의 Secret이 자동으로 안 바뀜 |
| `conservation-backend`/`conservation-guide-ai`가 `FATAL: database "conservation" does not exist`로 실패 | RDS 안에 `conservation` 데이터베이스가 실제로는 안 만들어져 있음 | 8단계 마지막의 임시 psql 파드로 접속해서 `CREATE DATABASE conservation;` 실행 |
| `conservation-backend`/`xray-ai`가 `ContainerCreating`에 오래 멈춤 | EFS 보안 그룹 2049 인바운드 누락(5단계) | `kubectl describe pod <pod>`의 `Events`에서 `MountVolume.SetUp failed` 확인 |

> `.env.k8s`에는 비밀번호가 `POSTGRES_PASSWORD=` 줄과 `DATABASE_URL=postgresql://conservation:<여기>@...` 안, **두 군데**에 들어간다. `conservation-backend`는 `POSTGRES_PASSWORD` 키를 따로 읽고(`k8s/app.yaml`의 `SPRING_DATASOURCE_PASSWORD`), `conservation-guide-ai`는 `DATABASE_URL`을 통째로 읽는다 — 비밀번호를 바꿀 땐 두 군데 다 고쳐야 한다.

### 서비스별 헬스체크
이 프로젝트엔 Spring Actuator가 안 붙어 있어서 `/actuator/health` 같은 표준 경로가 없다. 대신 실제 코드에 있는 엔드포인트로 확인한다:
```bash
kubectl port-forward svc/pottery-inspection-ai 8003:8000
curl http://localhost:8003/health
# {"status": "ok", "module_version": "pottery-inspection-v11"}

kubectl port-forward svc/conservation-backend 8080:8080
curl http://localhost:8080/api/xray/health
# 정상이면 {"aiServiceHealthy":true, ...} 형태로 xray-ai까지 연결된 상태가 확인됨
```
`pottery-inspection-ai`의 첫 요청은 Grounding DINO/SAM2 가중치를 Hugging Face Hub에서 처음 받는 시간이 걸리니(워커 노드가 아웃바운드 인터넷이 되는지도 이때 같이 확인), 첫 응답이 느려도 당황하지 않는다.

---

## 15단계 — 외부에서 API에 접속하는 방법

지금은 4개 서비스 모두 `type: ClusterIP`라 클러스터 밖에서 바로 접근이 안 된다.

**옵션 A — `kubectl port-forward` (즉시 테스트용)**
```bash
kubectl port-forward svc/conservation-backend 8080:8080
```
이 터미널을 유지하는 동안 로컬에서 `http://localhost:8080`으로 접속 가능. 터미널 닫으면 끊김, 임시 테스트 전용.

**옵션 B — `conservation-backend`만 `type: LoadBalancer`로 변경 (실제 외부 URL 필요 시)**
```yaml
spec:
  type: LoadBalancer   # ClusterIP → LoadBalancer
```
> **주의(실습 중 실제로 겪음)**: `k8s/app.yaml` 안에 `Service` 블록이 4개나 있어서, 엉뚱한 서비스(예: `conservation-guide-ai`)의 `type`을 바꾸기 쉽다. **반드시 `metadata.name: conservation-backend`인 `Service` 블록인지 확인**하고 고칠 것 — 나머지 세 서비스는 계속 `ClusterIP`로 남아있어야 한다(내부 전용이어야 하는 서비스가 실수로 외부에 노출되면 안 됨).

ALB Ingress Controller(Helm 필요) 설치 없이도 AWS가 자동으로 ELB를 만들어 `EXTERNAL-IP`를 부여한다(1~2분 정도 걸림). `conservation-backend`가 유일하게 외부에 노출되어야 하는 서비스이므로 이것만 바꾸면 된다. 도메인/HTTPS/경로 라우팅 같은 고급 기능이 필요해지면 그때 ALB Ingress로 업그레이드.

### `EXTERNAL-IP`로 실제 접속하는 방법
```bash
kubectl get svc conservation-backend
```
AWS에서는 이 `EXTERNAL-IP`가 실제 IP 주소가 아니라 **ELB의 긴 도메인 이름**(`a1b2c3...elb.amazonaws.com` 형태)으로 나오는 경우가 대부분이다. 브라우저에 넣을 땐:
- **포트를 꼭 붙인다**: `http://<EXTERNAL-IP>:8080` (`:8080` 없으면 기본 포트 80으로 요청이 가서 안 붙는다)
- **`https://`가 아니라 `http://`**: 아직 TLS 인증서를 설정하지 않았다.
- 루트(`/`)로 접속하면 등록된 라우팅이 없어 404가 뜰 수 있다 — 14단계의 헬스체크 엔드포인트(`/api/xray/health`)처럼 실제로 존재하는 경로로 접속해야 정상 응답을 볼 수 있다.

---

## 16단계 — 남은 작업

1. **ALB Ingress Controller 설치**: `conservation-backend`(관문 역할)만 외부 노출 — 콘솔에 설치 버튼이 없어 Helm 필요(유일하게 순수 콘솔 GUI로 안 되는 지점).
2. **IRSA로 S3 접근 전환 검토**: 지금 `.env.k8s`의 정적 `AWS_ACCESS_KEY_ID`/`SECRET` 대신, `conservation-backend`(사진 저장용 S3 접근)에 ServiceAccount 기반 IAM 역할을 연결. (`pottery-inspection-ai`는 11단계에서 S3 의존을 없앴으므로 이 항목과 무관.)
3. **CI/CD 파이프라인**: GitHub Actions(OIDC 기반 IAM 역할 연동, ECR push, `kubectl set image` 배포) 또는 AWS 콘솔 네이티브 CodePipeline + CodeBuild 중 선택.
4. **노드 디스크 여유 확인**: `pottery-inspection-ai` 이미지가 수 GB 단위라, 4개 서비스 이미지를 다 pull한 뒤 워커 노드(기본 20GB 디스크) 여유 공간을 확인. 부족하면 노드 그룹의 Disk size를 늘리거나 인스턴스 타입을 재검토.

---

## 부록 — 실습 중 나온 질문 Q&A

실제로 이 절차를 따라 하면서 나온 개념 질문들을 정리해둔다. 처음 이 문서를 보는 사람이 똑같이 헷갈릴 만한 지점들이라 남겨둔다.

**Q. CLI용으로 새로 만든 IAM 사용자한테 어떤 권한을 줘야 하나?**
A. 이 설정 작업 자체가 IAM/EKS/RDS/EFS/S3처럼 권한 범위가 넓어서, 처음부터 세세하게 권한을 골라 붙이기보다 `AdministratorAccess`를 가진 사용자 하나로 진행하는 게 실용적이다(1단계). CI/CD처럼 용도가 좁혀지는 나중 단계에서는 그때 가서 권한을 좁힌 별도 사용자를 새로 만들면 된다.

**Q. 콘솔에서 클러스터를 만든 계정으로 만든 IAM 사용자인데 왜 `kubectl` 권한이 없다고 하나?**
A. "같은 AWS 계정"과 "같은 IAM 사용자(신원)"는 다른 개념이다. 계정은 회사, IAM 사용자는 그 회사 소속 직원 개개인에 비유할 수 있다 — 같은 회사 소속이어도 직원마다 사무실 열쇠(EKS Access Entry)를 따로 받아야 한다. 콘솔에 로그인했던 신원과 CLI용으로 새로 만든 IAM 사용자는 서로 다른 신원이라, 후자에게 EKS Access Entry를 별도로 만들어줘야 한다(9단계).

**Q. EKS의 "IAM 액세스 항목(Access entry)"이 정확히 뭔가?**
A. "이 AWS 계정의 어떤 사용자/역할이 이 EKS 클러스터 안에서 뭘 할 수 있는지"를 등록하는 목록이다. IAM 정책이 "AWS 서비스들에 대해 뭘 할 수 있는지"(클러스터 바깥)를 정하는 것이라면, Access entry는 "클러스터 안에 들어와서 쿠버네티스 명령을 실행할 수 있는지"(클러스터 안쪽)를 정하는 것 — 완전히 분리된 권한 체계라 둘 다 있어야 `kubectl`이 동작한다.

**Q. OIDC 공급자 연동은 왜 하는 건가?**
A. 클러스터 안에서 도는 특정 Pod가 AWS 자원(EFS 등)에 접근할 권한을 받으려면, AWS IAM이 "이 쿠버네티스 클러스터가 발급한 신분증(Service Account 토큰)은 믿을 만하다"고 미리 알아야 한다. OIDC 연동은 그 신뢰 관계만 뚫어두는 것이고, 실제 권한(무엇을 할 수 있는지)은 그다음 단계(IAM 역할)에서 별도로 부여한다(6~7단계).

**Q. EFS CSI 드라이버의 `controller`/`node`는 쿠버네티스의 master/worker 노드 같은 구조인가?**
A. 비슷한 직관(조율하는 소수 vs 각 기계에서 실무하는 다수)이지만 배치되는 위치가 다르다. EKS의 마스터(컨트롤 플레인)는 AWS가 완전히 관리해서 사용자가 아예 접근할 수 없는 영역이고, `efs-csi-controller`/`efs-csi-node`는 **둘 다 평범한 워커 노드 위에서 도는 파드**다. 다만 controller는 조율 역할이라 1~2개만 뜨고, node는 각 워커 노드에 실제로 마운트 작업을 해야 해서 노드 수만큼(DaemonSet) 하나씩 자동 배치된다(7단계).

**Q. `pottery-inspection-ai`가 쓰는 `transformers`가 뭔가, 방금 이미지에 넣은 CNN 모델과 관련 있나?**
A. `transformers`는 Hugging Face가 만든 라이브러리로, 미리 학습된 AI 모델을 공통된 방식으로 불러다 쓰게 해주는 프레임워크다. 이 프로젝트에서는 Grounding DINO(사진에서 도자기 위치를 찾는 모델)를 불러오는 데 쓰이고, 실행 시점에 Hugging Face Hub에서 자동 다운로드된다. 11단계에서 이미지에 구워 넣은 `pottery_multitask_model_v2`(시대 판정 CNN)는 이 팀이 직접 학습시킨 별개의 모델이라 서로 무관하다.

**Q. RDS의 "DB 식별자"(`conservation-db`)와 "데이터베이스"(`conservation`)가 같은 건가?**
A. 아니다. DB 식별자는 서버(인스턴스) 자체의 이름이고, 데이터베이스는 그 서버 안에 들어있는 개별 저장 단위다(8단계 참고 — 캐비닛과 서랍 비유). 이름이 비슷해서 헷갈리기 쉽다.

**Q. `kubectl get svc`의 `EXTERNAL-IP`를 브라우저에 그냥 입력하면 되나?**
A. 형태는 맞지만 포트(`:8080`)를 붙여야 하고, TLS를 아직 설정 안 했으니 `https`가 아니라 `http`로 접속해야 한다. 또 AWS에서는 이 값이 실제 IP가 아니라 ELB의 긴 도메인 이름인 경우가 많다(15단계).
