# 최종 통합 코드 검증 보고서

검증 기준: 2026-08-06 · 업로드된 최신 `main` ZIP을 기준으로 S3·Lambda·RDS 구조를 통합한 소스

## AWS 실환경 검증 업데이트 — 2026-08-07

### 검증 성공

- Spring Backend의 `POST /api/xray/stitch/jobs/prepare` 호출 성공
- X-RAY 작업 생성 및 상태 `PENDING` 저장 성공
- 컬러 이미지 1개와 X-RAY 이미지 2개의 Presigned PUT URL 발급 성공
- Presigned URL을 이용한 S3 직접 업로드 성공
- 컬러 이미지와 X-RAY 이미지 업로드 응답 HTTP 200 확인
- S3 `ObjectCreated` 이벤트를 통한 Lambda 자동 실행 성공
- Lambda의 Private RDS PostgreSQL 연결 성공
- `public.s3_file`에 실제 S3 객체 정보 저장 및 `COMPLETED` 상태 확인

### 현재 미완료

- FastAPI 결합 작업 자체는 실행됨
- `assembled_xray.png` 생성 후 S3 Presigned PUT 업로드에서 HTTP 400 발생
- 오류 메시지:
  `Upload failed: status=400, file=assembled_xray.png`
- 따라서 `layout.json`, `report.json`, `finalization_bundle.zip`을 포함한
  전체 outputs 저장 및 최종 callback 완료 흐름은 아직 검증되지 않음

### 이번 검증에서 제외한 범위

- 실제 FE 연동
- EKS 배포
- 수동 위치 보정 이후 final outputs
- 운영 DB 마이그레이션

## 검증 완료

| 검증 항목                          | 결과                                                            |
| ---------------------------------- | --------------------------------------------------------------- |
| Python 문법 컴파일                 | `ai-services/xray-ai/app`, Lambda 소스 전체 통과                |
| FastAPI 계약·보안·통합 흐름 테스트 | **7/7 통과**                                                    |
| Lambda S3 키 파서 테스트           | **3/3 통과**                                                    |
| YAML 파싱                          | `application.yaml`, Compose, K8s 9문서 포함 총 11문서 통과      |
| Docker Compose 서비스 구성         | 5개 서비스 확인                                                 |
| K8s 환경변수 중복                  | 없음                                                            |
| merge conflict marker              | 없음                                                            |
| X-ray `/shared`·EFS·PVC 구현 참조  | 없음                                                            |
| Java 소스                          | 47개 파일, 내부 import 누락 0개                                 |
| 핵심 Java 집중 컴파일              | DTO·도메인·Repository·S3 Key·Stitch/Defect Mapping Service 통과 |
| 전체 Java 구문 진단                | 외부 라이브러리 없는 raw `javac`에서 구문성 오류 0개            |
| Lambda ZIP 빌드 스크립트           | `bash -n` 통과                                                  |

### FastAPI 테스트 범위

1. URL 기반 결합 요청 계약
2. URL 기반 최종화 요청 계약
3. Docker 내부 callback hostname 허용
4. 대용량 파일 streaming 업로드·다운로드
5. 다운로드 용량 제한 및 부분 파일 정리
6. ZIP path traversal·symlink 차단
7. mocked `결합 → S3 산출물 → callback → bundle → 최종화 → provenance → callback` 왕복 흐름

### Lambda 테스트 범위

- 원본 X-ray·컬러 입력 키 파싱
- 결합·최종화 전체 출력 키 파싱
- X-ray 규칙에 맞지 않는 키 무시

## 실제 실행을 완료하지 못한 항목

### Gradle 전체 컴파일

`./gradlew compileJava`를 실행했지만 현재 환경에서 Gradle 배포본을 내려받지 못했다.

```text
java.net.UnknownHostException: services.gradle.org
```

따라서 실제 Spring 의존성을 포함한 `compileJava`는 인터넷이 가능한 개발 PC 또는 CI에서 다시 실행해야 한다. 대신 핵심 변경 Java 파일은 임시 외부 API stub을 사용한 집중 컴파일을 통과했고, 전체 Java 파일은 구문성 오류가 없는지 별도로 검사했다.

### Docker 이미지 빌드

현재 실행환경에 Docker CLI가 설치되어 있지 않아 `docker compose build`와 컨테이너 기동 테스트는 수행하지 못했다.

### AWS 클라우드 E2E

현재 환경에 실제 AWS 자격증명·S3 버킷·RDS·Lambda·EKS가 연결되어 있지 않아 아래는 팀 AWS 환경에서 검증해야 한다.

1. `db/xray_s3_rds.sql` 또는 팀 migration 적용
2. Lambda 패키지 배포 및 `xray/` S3 트리거 연결
3. Spring IAM 역할의 S3 권한 확인
4. `prepare → presigned PUT → start → FastAPI → S3 → callback` 전체 흐름
5. Konva 최종 layout → 최종 렌더링 → provenance → 결함 매핑
6. callback 차단 후 `/reconcile` 복구

### X-ray 결함 모델

업로드된 최신 main ZIP에는 아래 가중치가 없고 `models/README.md`만 존재한다.

```text
ai-services/xray-ai/models/final_xray_defect_best.pt
```

따라서 실제 YOLO 결함 추론은 별도 가중치를 추가한 뒤 검증해야 한다. 육안 조사 모델 파일은 원본 main에 포함된 상태로 보존했다.

## 배포 전 권장 명령

```bash
./gradlew clean test compileJava

docker compose config
docker compose build --no-cache
docker compose up -d

docker compose ps
docker compose logs -f conservation-backend xray-ai
```

AWS 연결 후에는 `tools/test_xray_s3_flow.py`로 Spring 기준 기본 S3 흐름을 확인할 수 있다.
