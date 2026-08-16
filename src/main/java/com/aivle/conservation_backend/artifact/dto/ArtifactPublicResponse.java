package com.aivle.conservation_backend.artifact.dto;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import lombok.Getter;

import java.time.LocalDateTime;
import java.util.UUID;

@Getter
public class ArtifactPublicResponse {

    private final UUID artifactId;
    private final String name;
    private final String category;
    private final String material;
    private final String era;
    private final String description;
    private final String representativeImageUrl;
    private final LocalDateTime createdAt;
    private final LocalDateTime updatedAt;

    public ArtifactPublicResponse(Artifact artifact, String representativeImageUrl) {
        this.artifactId = artifact.getId();
        this.name = artifact.getName();
        this.category = artifact.getCategory();
        this.material = artifact.getMaterial();
        this.era = artifact.getEra();
        this.description = artifact.getDescription();
        this.representativeImageUrl = representativeImageUrl;
        this.createdAt = artifact.getCreatedAt();
        this.updatedAt = artifact.getUpdatedAt();
    }
}
