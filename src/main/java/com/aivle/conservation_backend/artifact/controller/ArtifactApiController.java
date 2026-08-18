package com.aivle.conservation_backend.artifact.controller;

import com.aivle.conservation_backend.artifact.dto.AddArtifactRequest;
import com.aivle.conservation_backend.artifact.dto.ArtifactResponse;
import com.aivle.conservation_backend.artifact.dto.ArtifactPublicResponse;
import com.aivle.conservation_backend.artifact.dto.ArtifactWorkflowStatusResponse;
import com.aivle.conservation_backend.artifact.dto.UpdateArtifactRequest;
import com.aivle.conservation_backend.artifact.service.ArtifactService;
import com.aivle.conservation_backend.artifact.service.ArtifactWorkflowStatusService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.UUID;

@RequiredArgsConstructor
@RestController
@RequestMapping("/api/artifacts")
public class ArtifactApiController {

    private final ArtifactService artifactService;
    private final ArtifactWorkflowStatusService artifactWorkflowStatusService;

    @PostMapping
    public ResponseEntity<ArtifactResponse> create(
            @Valid
            @RequestBody
            AddArtifactRequest request
    ) {
        return ResponseEntity
                .status(HttpStatus.CREATED)
                .body(
                        artifactService.save(request)
                );
    }

    @GetMapping
    public ResponseEntity<List<ArtifactResponse>> findAll() {

        return ResponseEntity.ok(
                artifactService.findAll()
        );
    }

    @GetMapping("/mine")
    public ResponseEntity<List<ArtifactResponse>> findMine() {
        return ResponseEntity.ok(artifactService.findMine());
    }

    @GetMapping("/public")
    public ResponseEntity<List<ArtifactPublicResponse>> findPublic() {
        return ResponseEntity.ok(artifactService.findPublic());
    }

    @GetMapping("/{artifactId}")
    public ResponseEntity<ArtifactResponse> findById(
            @PathVariable UUID artifactId
    ) {
        return ResponseEntity.ok(
                artifactService.findById(
                        artifactId
                )
        );
    }

    @GetMapping("/{artifactId}/workflow-status")
    public ResponseEntity<ArtifactWorkflowStatusResponse> workflowStatus(
            @PathVariable UUID artifactId
    ) {
        return ResponseEntity.ok(artifactWorkflowStatusService.get(artifactId));
    }

    @PatchMapping("/{artifactId}")
    public ResponseEntity<ArtifactResponse> update(
            @PathVariable UUID artifactId,

            @Valid
            @RequestBody
            UpdateArtifactRequest request
    ) {
        return ResponseEntity.ok(
                artifactService.update(
                        artifactId,
                        request
                )
        );
    }

    @PostMapping(
            value = "/{artifactId}/representative-image",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE
    )
    public ResponseEntity<ArtifactResponse>
    uploadRepresentativeImage(

            @PathVariable
            UUID artifactId,

            @RequestParam("file")
            MultipartFile file
    ) {
        return ResponseEntity.ok(
                artifactService
                        .uploadRepresentativeImage(
                                artifactId,
                                file
                        )
        );
    }

    @DeleteMapping("/{artifactId}")
    public ResponseEntity<Void> delete(
            @PathVariable UUID artifactId
    ) {
        artifactService.delete(artifactId);

        return ResponseEntity.noContent().build();
    }
}