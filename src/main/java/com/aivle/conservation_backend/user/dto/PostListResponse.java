package com.aivle.conservation_backend.user.dto;

import com.aivle.conservation_backend.user.domain.Post;
import lombok.Getter;

import java.time.LocalDateTime;

@Getter
public class PostListResponse {

    private final Long id;
    private final String title;
    private final String author;
    private final long viewCount;
    private final LocalDateTime createdAt;

    public PostListResponse(Post post) {
        this.id = post.getId();
        this.title = post.getTitle();
        this.author = maskNickname(post.getAuthor().getNickname());
        this.viewCount = post.getViewCount();
        this.createdAt = post.getCreatedAt();
    }

    private String maskNickname(String nickname) {
        if (nickname == null || nickname.isBlank()) {
            return "사용자";
        }

        return nickname.substring(0, 1) + "**";
    }
}