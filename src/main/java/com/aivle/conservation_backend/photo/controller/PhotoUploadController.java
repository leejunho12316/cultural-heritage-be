package com.aivle.conservation_backend.photo.controller;

import com.aivle.conservation_backend.artifact.service.ArtifactAccessService;
import com.aivle.conservation_backend.photo.dto.PhotoUploadResponseDto;
import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.UUID;

// FE가 사진을 업로드하면 S3에 저장하고, photo_urls 등에 그대로 넣을 수 있는 URL을 반환.
@RequiredArgsConstructor
@RestController
@RequestMapping("/photos")
public class PhotoUploadController {

    private final S3PhotoStorageService storageService;
    private final ArtifactAccessService artifactAccessService;

    @PostMapping(value = "/upload", consumes = "multipart/form-data")
    public PhotoUploadResponseDto upload(
            @RequestParam("file") MultipartFile file,
            @RequestParam("artifactId") UUID artifactId
    ) {
        artifactAccessService.requireArtifact(artifactId);
        S3PhotoStorageService.StoredPhoto stored = storageService.uploadStored(artifactId, file);
        return new PhotoUploadResponseDto(stored.url(), stored.key());
    }

    /** 페이지 재진입 시 DB/checkpoint에 저장한 S3 key로 새 표시 URL을 발급한다. */
    @GetMapping("/url")
    public PhotoUploadResponseDto refreshUrl(
            @RequestParam("artifactId") UUID artifactId,
            @RequestParam("key") String key
    ) {
        artifactAccessService.requireArtifact(artifactId);
        String url = storageService.presignedArtifactUploadUrl(artifactId, key);
        return new PhotoUploadResponseDto(url, key);
    }
}
