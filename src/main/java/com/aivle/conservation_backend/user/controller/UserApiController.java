package com.aivle.conservation_backend.user.controller;

import com.aivle.conservation_backend.user.dto.AddUserRequest;
import com.aivle.conservation_backend.user.dto.LoginRequest;
import com.aivle.conservation_backend.user.dto.LoginResponse;
import com.aivle.conservation_backend.user.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RequiredArgsConstructor
@RestController
public class UserApiController {

    private final UserService userService;

    @PostMapping("/api/users/signup") // 또는 /user
    public ResponseEntity<Long> signup( @Valid @RequestBody AddUserRequest request) {
        Long userId = userService.save(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(userId);
    }

    @PostMapping("/api/users/login")
    public ResponseEntity<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        String token = userService.login(request);
        return ResponseEntity.ok(new LoginResponse(token));
    }

    @PostMapping("/api/users/logout")
    public ResponseEntity<Void> logout() {
        return ResponseEntity.noContent().build();
    }
}
