package com.aivle.conservation_backend.user.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
public class UpdatePostRequest {

    @NotBlank(message = "게시글 제목은 필수입니다.")
    @Size(max = 200, message = "게시글 제목은 200자 이하여야 합니다.")
    private String title;

    @NotBlank(message = "게시글 내용은 필수입니다.")
    private String content;
}