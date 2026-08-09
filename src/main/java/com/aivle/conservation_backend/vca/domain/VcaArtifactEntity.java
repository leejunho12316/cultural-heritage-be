package com.aivle.conservation_backend.vca.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;

// VCA 대상 유물의 영속 상태. 팀 공유 ERD의 `artifact` 테이블.
// `artifactCode`가 Spring VCA API가 지금 쓰는 자유 문자열 artifactId(예: "demo-artifact")이고,
// `id`는 다른 테이블이 참조하는 내부 uuid PK다.
@Entity
@Table(name = "artifact")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class VcaArtifactEntity {

    @Id
    @Column(name = "id")
    private UUID id;

    @Column(name = "artifact_code", unique = true, nullable = false)
    private String artifactCode;

    @Column(name = "title")
    private String title;

    @Column(name = "description")
    private String description;

    @Column(name = "representative_image_key")
    private String representativeImageKey;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "metadata_json", columnDefinition = "jsonb")
    private Map<String, Object> metadataJson;

    @Column(name = "created_at")
    private Instant createdAt;

    // ERD에는 없지만 FE 목록/상세 응답의 "마지막 활동 시각"에 필요해서 추가한 컬럼.
    @Column(name = "updated_at")
    private Instant updatedAt;
}
