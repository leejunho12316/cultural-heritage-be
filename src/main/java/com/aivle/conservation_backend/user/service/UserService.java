package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.common.config.jwt.JwtTokenProvider;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.AddUserRequest;
import com.aivle.conservation_backend.user.dto.LoginRequest;
import com.aivle.conservation_backend.user.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

@RequiredArgsConstructor
@Service
public class UserService {

    private final UserRepository userRepository;
    private final BCryptPasswordEncoder bCryptPasswordEncoder;
    private final JwtTokenProvider jwtTokenProvider;

    public Long save(AddUserRequest request) {
        if (userRepository.findByEmail(request.getEmail()).isPresent()) {
            throw new IllegalArgumentException("이미 사용 중인 이메일입니다.");
        }
        String encodedPassword = bCryptPasswordEncoder.encode(request.getPassword());
        return userRepository.save(request.toEntity(encodedPassword)).getId();
    }

    public String login(LoginRequest request) {
        User user = userRepository.findByEmail(request.getEmail())
                .orElseThrow(() -> new IllegalArgumentException("가입되지 않은 이메일입니다."));

        if (!bCryptPasswordEncoder.matches(request.getPassword(), user.getPassword())) {
            throw new IllegalArgumentException("비밀번호가 일치하지 않습니다.");
        }


        return jwtTokenProvider.createToken(user.getEmail(), user.getRole().name());
    }
}
