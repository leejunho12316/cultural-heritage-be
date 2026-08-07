package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.user.domain.Post;
import com.aivle.conservation_backend.user.domain.PostSearchType;
import com.aivle.conservation_backend.user.domain.Role;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.*;
import com.aivle.conservation_backend.user.repository.PostRepository;
import com.aivle.conservation_backend.user.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class PostService {

    private final PostRepository postRepository;
    private final UserRepository userRepository;

    public PostPageResponse findAll(
            PostSearchType searchType,
            String keyword,
            Pageable pageable
    ) {
        Page<Post> posts;

        if (keyword == null || keyword.isBlank()) {
            posts = postRepository.findAll(pageable);
        } else if (searchType == PostSearchType.AUTHOR) {
            posts = postRepository
                    .findByAuthor_NicknameContainingIgnoreCase(
                            keyword.trim(),
                            pageable
                    );
        } else {
            posts = postRepository
                    .findByTitleContainingIgnoreCase(
                            keyword.trim(),
                            pageable
                    );
        }

        return new PostPageResponse(posts);
    }

    @Transactional
    public PostDetailResponse findById(Long id) {
        Post post = findPost(id);

        post.increaseViewCount();

        return new PostDetailResponse(post);
    }

    @Transactional
    public PostDetailResponse save(
            AddPostRequest request,
            String email
    ) {
        User author = findUser(email);

        Post post = Post.builder()
                .title(request.getTitle())
                .content(request.getContent())
                .author(author)
                .build();

        Post savedPost = postRepository.save(post);

        return new PostDetailResponse(savedPost);
    }

    @Transactional
    public PostDetailResponse update(
            Long id,
            UpdatePostRequest request,
            String email
    ) {
        Post post = findPost(id);
        User currentUser = findUser(email);

        validateOwnerOrAdmin(post, currentUser);

        post.update(
                request.getTitle(),
                request.getContent()
        );

        return new PostDetailResponse(post);
    }

    @Transactional
    public void delete(Long id, String email) {
        Post post = findPost(id);
        User currentUser = findUser(email);

        validateOwnerOrAdmin(post, currentUser);

        postRepository.delete(post);
    }

    private Post findPost(Long id) {
        return postRepository.findPostById(id)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "존재하지 않는 게시글입니다."
                        ));
    }

    private User findUser(String email) {
        return userRepository.findByEmail(email)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.UNAUTHORIZED,
                                "로그인 사용자 정보를 찾을 수 없습니다."
                        ));
    }

    private void validateOwnerOrAdmin(
            Post post,
            User currentUser
    ) {
        boolean isAdmin =
                currentUser.getRole() == Role.ADMIN;

        boolean isOwner =
                post.isWrittenBy(currentUser.getId());

        if (!isAdmin && !isOwner) {
            throw new ResponseStatusException(
                    HttpStatus.FORBIDDEN,
                    "게시글을 수정하거나 삭제할 권한이 없습니다."
            );
        }
    }

    public PostPageResponse findMyPosts(
            Long userId,
            Pageable pageable
    ) {
        Page<Post> posts =
                postRepository.findByAuthor_Id(userId, pageable);

        return new PostPageResponse(posts);
    }
}