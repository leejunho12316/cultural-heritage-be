package com.aivle.conservation_backend.user.dto;

import lombok.AllArgsConstructor;
import lombok.Getter;

@AllArgsConstructor
@Getter
public class LoginResponse {
    private String token;
    private String loginId;
    private String email;
    private String nickName;
    private String role;
}
