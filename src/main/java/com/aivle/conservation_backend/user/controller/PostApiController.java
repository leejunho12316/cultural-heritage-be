package com.aivle.conservation_backend.user.controller;

import com.aivle.conservation_backend.user.domain.PostSearchType;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.*;
import com.aivle.conservation_backend.user.service.PostService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.web.PageableDefault;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RequiredArgsConstructor
@RestController
@RequestMapping("/api/posts")
public class PostApiController {

    private final PostService postService;

    @GetMapping
    public ResponseEntity<PostPageResponse> findAllPosts(
            @RequestParam(
                    required = false,
                    defaultValue = "TITLE"
            )
            PostSearchType searchType,

            @RequestParam(
                    required = false,
                    defaultValue = ""
            )
            String keyword,

            @PageableDefault(
                    size = 10,
                    sort = "createdAt",
                    direction = Sort.Direction.DESC
            )
            Pageable pageable
    ) {
        return ResponseEntity.ok(
                postService.findAll(
                        searchType,
                        keyword,
                        pageable
                )
        );
    }

    @GetMapping("/{id}")
    public ResponseEntity<PostDetailResponse> findPost(
            @PathVariable Long id
    ) {
        return ResponseEntity.ok(
                postService.findById(id)
        );
    }

    @PostMapping
    public ResponseEntity<PostDetailResponse> addPost(
            @AuthenticationPrincipal User currentUser,
            @Valid @RequestBody AddPostRequest request
    ) {
        PostDetailResponse response =
                postService.save(
                        request,
                        currentUser.getEmail()
                );

        return ResponseEntity
                .status(HttpStatus.CREATED)
                .body(response);
    }

    @PutMapping("/{id}")
    public ResponseEntity<PostDetailResponse> updatePost(
            @PathVariable Long id,
            @AuthenticationPrincipal User currentUser,
            @Valid @RequestBody UpdatePostRequest request
    ) {
        return ResponseEntity.ok(
                postService.update(
                        id,
                        request,
                        currentUser.getEmail()
                )
        );
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deletePost(
            @PathVariable Long id,
            @AuthenticationPrincipal User currentUser
    ) {
        postService.delete(
                id,
                currentUser.getEmail()
        );

        return ResponseEntity.noContent().build();
    }

    @GetMapping("/me")
    public ResponseEntity<PostPageResponse> findMyPosts(
            @AuthenticationPrincipal User currentUser,

            @PageableDefault(
                    size = 10,
                    sort = "createdAt",
                    direction = Sort.Direction.DESC
            )
            Pageable pageable
    ) {
        return ResponseEntity.ok(
                postService.findMyPosts(
                        currentUser.getId(),
                        pageable
                )
        );
    }
}