package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.common.config.jwt.JwtTokenProvider;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.AddUserRequest;
import com.aivle.conservation_backend.user.dto.LoginRequest;
import com.aivle.conservation_backend.user.repository.PostRepository;
import com.aivle.conservation_backend.user.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@RequiredArgsConstructor
@Service
public class UserService {

    private final UserRepository userRepository;
    private final BCryptPasswordEncoder bCryptPasswordEncoder;
    private final JwtTokenProvider jwtTokenProvider;
    private final PostRepository postRepository;

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

    @Transactional
    public void withdraw(
            Long userId,
            String rawPassword
    ) {
        User user = userRepository.findById(userId)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "사용자를 찾을 수 없습니다."
                        )
                );

        if (!bCryptPasswordEncoder.matches(
                rawPassword,
                user.getPassword()
        )) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "비밀번호가 일치하지 않습니다."
            );
        }

        // 외래키 충돌 방지를 위해 게시글을 먼저 삭제
        postRepository.deleteAllByAuthorId(user.getId());

        userRepository.delete(user);
    }
}
