package com.aivle.conservation_backend.user.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@NoArgsConstructor
@AllArgsConstructor
@Getter
public class UpdateNoticeRequest {
    @NotBlank
    private String title;
    @NotBlank
    private String content;

    private Boolean isPinned;

}
