package com.aivle.conservation_backend.artifact.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.UUID;

import com.aivle.conservation_backend.user.domain.User;

@Entity
@Table(name = "artifacts")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Artifact {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    @Column(name = "artifact_id", updatable = false, nullable = false)
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User owner;

    @Column(nullable = false, length = 200)
    private String name;

    @Column(length = 100)
    private String category;

    @Column(length = 100)
    private String material;

    @Column(length = 100)
    private String era;

    @Column(columnDefinition = "TEXT")
    private String description;

    @Column(name = "condition_summary", columnDefinition = "TEXT")
    private String conditionSummary;

    @Column(length = 100)
    private String weight;

    @Column(name = "bonding_area", length = 255)
    private String bondingArea;

    @Column(name = "treatment_purpose", length = 255)
    private String treatmentPurpose;

    @Column(name = "representative_image_key", length = 1000)
    private String representativeImageKey;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @Builder
    public Artifact(
            User owner,
            String name,
            String category,
            String material,
            String era,
            String description,
            String conditionSummary,
            String weight,
            String bondingArea,
            String treatmentPurpose
    ) {
        this.owner = owner;
        this.name = name;
        this.category = category;
        this.material = material;
        this.era = era;
        this.description = description;
        this.conditionSummary = conditionSummary;
        this.weight = weight;
        this.bondingArea = bondingArea;
        this.treatmentPurpose = treatmentPurpose;
    }

    public void update(
            String name,
            String category,
            String material,
            String era,
            String description,
            String conditionSummary,
            String weight,
            String bondingArea,
            String treatmentPurpose
    ) {
        this.name = name;
        this.category = category;
        this.material = material;
        this.era = era;
        this.description = description;
        this.conditionSummary = conditionSummary;
        this.weight = weight;
        this.bondingArea = bondingArea;
        this.treatmentPurpose = treatmentPurpose;
    }

    public void updateRepresentativeImage(
            String representativeImageKey
    ) {
        this.representativeImageKey = representativeImageKey;
    }

    @PrePersist
    protected void onCreate() {
        LocalDateTime now = LocalDateTime.now();

        this.createdAt = now;
        this.updatedAt = now;
    }

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }
}