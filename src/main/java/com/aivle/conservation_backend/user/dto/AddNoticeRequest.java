package com.aivle.conservation_backend.user.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@NoArgsConstructor
@AllArgsConstructor
@Getter
public class AddNoticeRequest {

    @NotBlank(message = "공지사항 제목은 필수 입력 항목입니다.")
    private String title;

    @NotBlank(message = "공지사항 내용은 필수 입력 항목입니다.")
    private String content;

    private Boolean isPinned;
}