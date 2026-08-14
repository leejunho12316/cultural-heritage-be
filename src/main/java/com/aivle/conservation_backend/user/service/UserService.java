package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.common.config.jwt.JwtTokenProvider;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.AddUserRequest;
import com.aivle.conservation_backend.user.dto.LoginRequest;
import com.aivle.conservation_backend.user.dto.LoginResponse;
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
        if (userRepository.findByLoginId(request.getLoginId()).isPresent()) {
            throw new IllegalArgumentException("이미 사용 중인 아이디입니다.");
        }
        if (userRepository.findByEmail(request.getEmail()).isPresent()) {
            throw new IllegalArgumentException("이미 사용 중인 이메일입니다.");
        }

        String encodedPassword = bCryptPasswordEncoder.encode(request.getPassword());

        return userRepository.save(request.toEntity(encodedPassword)).getId();
    }

    public LoginResponse login(LoginRequest request) {
        User user = userRepository.findByLoginId(request.getLoginId())
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 사용자입니다."));

        if (!bCryptPasswordEncoder.matches(request.getPassword(), user.getPassword())) {
        throw new IllegalArgumentException("비밀번호가 일치하지 않습니다.");
        }

        String token = jwtTokenProvider.createToken(
                user.getLoginId(),
                user.getRole().name()
        );

        return new LoginResponse(
                token,
                user.getLoginId(),
                user.getEmail(),
                user.getNickname(),
                user.getRole()
        );
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

        postRepository.deleteAllByAuthorId(user.getId());
        userRepository.delete(user);
    }
}
