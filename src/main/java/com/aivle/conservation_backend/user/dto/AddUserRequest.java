package com.aivle.conservation_backend.user.dto;

import com.aivle.conservation_backend.user.domain.Role;
import com.aivle.conservation_backend.user.domain.User;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@AllArgsConstructor
public class AddUserRequest {

    @NotBlank(message = "이메일은 필수입니다.")
    @Email(message = "이메일 형식이 올바르지 않습니다.")
    private String email;

    @NotBlank(message = "비밀번호는 필수입니다.")
    @Size(min = 8, max = 100, message = "비밀번호는 8자 이상이어야 합니다.")
    private String password;

    @NotBlank(message = "닉네임은 필수입니다.")
    @Size(max = 30, message = "닉네임은 30자 이하여야 합니다.")
    private String nickName;

    private Role role;

    public User toEntity(String encodedPassword) {
        return User.builder()
                .email(email)
                .password(encodedPassword)
                .nickname(nickName)
                .role(Role.USER)
                .build();
    }
}
