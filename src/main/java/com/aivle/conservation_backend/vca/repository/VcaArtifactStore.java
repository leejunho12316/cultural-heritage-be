package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.VcaArtifactEntity;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

// VcaService가 실제로 필요로 하는 최소 조작만 노출하는 좁은 인터페이스.
// 운영에서는 Spring Data JPA가 구현하고, 단위 테스트에서는 순수 in-memory 구현으로 대체된다.
public interface VcaArtifactStore {

    VcaArtifactEntity save(VcaArtifactEntity entity);

    Optional<VcaArtifactEntity> findById(UUID id);

    List<VcaArtifactEntity> findAll();
}
