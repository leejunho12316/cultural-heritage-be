package com.aivle.conservation_backend.vca.repository;

import com.aivle.conservation_backend.vca.domain.UploadedImage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface UploadedImageRepository
        extends JpaRepository<UploadedImage, UUID>, UploadedImageStore {

    // UploadedImageStore.save/findById/deleteById가 CrudRepository의 제네릭
    // 버전과 동시에 상속돼 그냥 두면 호출부에서 ambiguous 컴파일 오류가 난다 -
    // 명시적으로 재선언해 하나로 합쳐준다.
    @Override
    UploadedImage save(UploadedImage entity);

    @Override
    Optional<UploadedImage> findById(UUID id);

    @Override
    void deleteById(UUID id);

    @Override
    List<UploadedImage> findByArtifactId(UUID artifactId);

    // artifact 삭제 캐스케이드가 이 artifact에 업로드된 이미지를 표시 순서대로
    // 훑어 S3 정리 대상 key를 모으는 데 쓴다.
    List<UploadedImage> findAllByArtifactIdOrderByDisplayOrderAscCreatedAtAsc(UUID artifactId);
}
