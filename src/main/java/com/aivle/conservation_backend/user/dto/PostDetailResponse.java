package com.aivle.conservation_backend.user.dto;

import com.aivle.conservation_backend.user.domain.Post;
import lombok.Getter;

import java.time.LocalDateTime;

@Getter
public class PostDetailResponse {

    private final Long id;
    private final String title;
    private final String content;
    private final String author;
    private final long viewCount;
    private final LocalDateTime createdAt;
    private final LocalDateTime updatedAt;

    public PostDetailResponse(Post post) {
        this.id = post.getId();
        this.title = post.getTitle();
        this.content = post.getContent();
        this.author = maskNickname(post.getAuthor().getNickname());
        this.viewCount = post.getViewCount();
        this.createdAt = post.getCreatedAt();
        this.updatedAt = post.getUpdatedAt();
    }

    private String maskNickname(String nickname) {
        if (nickname == null || nickname.isBlank()) {
            return "사용자";
        }

        return nickname.substring(0, 1) + "**";
    }
}