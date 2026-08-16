package com.aivle.conservation_backend.user.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
public class WithdrawUserRequest {

    @NotBlank(message = "비밀번호는 필수입니다.")
    private String password;
}