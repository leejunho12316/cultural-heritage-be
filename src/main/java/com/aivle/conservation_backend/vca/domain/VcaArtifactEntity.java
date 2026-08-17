package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;
import java.util.UUID;

// VCA 대상 유물의 영속 상태. 팀이 이미 다른 기능(artifact 패키지)에서 쓰고 있는
// `artifacts` 테이블을 그대로 공유한다 - VCA는 그 테이블의 컬럼 중 실제로 쓰는
// 것만(id/name/createdAt/updatedAt/userId) 매핑한다. category/material/era 등
// 다른 컬럼은 VCA가 다루지 않으므로 이 엔티티에 없고, Hibernate가 이 엔티티로
// 만드는 INSERT/UPDATE는 여기 매핑된 컬럼만 건드리므로 다른 기능이 채운 값을
// 지우지 않는다.
// userId는 db/artifact_owner_migration.sql이 이 테이블에 NOT NULL + users FK로
// 걸어둔 컬럼이다 - 예전에는 이 필드가 없어서 그 제약이 걸린 환경(운영 RDS)에서
// VCA 유물 생성이 매번 500으로 실패했다. VcaService.createArtifact가 현재
// 로그인 사용자 id로 채운다.
// PK가 서버에서 자동 생성되는 UUID라 클라이언트가 URL에 미리 넣을 값을 알 수
// 없다 - VCA API는 `POST /api/vca`로 먼저 생성해 이 id를 응답받은 뒤, 이후
// 모든 하위 경로에서 그 UUID를 쓰는 표준 REST 패턴을 따른다(팀의
// ArtifactApiController와 동일한 패턴).
@Entity
@Table(name = "artifacts")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaArtifactEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    @Column(name = "artifact_id", updatable = false, nullable = false)
    private UUID id;

    @Column(name = "name", nullable = false, length = 200)
    private String name;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @Column(name = "user_id")
    private Long userId;
}
