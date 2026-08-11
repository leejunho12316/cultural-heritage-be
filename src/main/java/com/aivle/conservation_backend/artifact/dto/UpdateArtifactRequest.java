package com.aivle.conservation_backend.artifact.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
public class UpdateArtifactRequest {

    @NotBlank(message = "유물명은 필수입니다.")
    @Size(max = 200, message = "유물명은 200자 이하여야 합니다.")
    private String name;

    private String category;
    private String material;
    private String era;
    private String description;
    private String conditionSummary;
    private String weight;
    private String bondingArea;
    private String treatmentPurpose;
}