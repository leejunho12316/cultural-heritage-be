package com.aivle.conservation_backend.user.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Getter
public class Notice {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "id", updatable = false)
    private Long id;

    @Column(name = "title", nullable = false)
    private String title;

    @Column(name = "content",nullable = false)
    private String content;

    @Column(name = "is_pinned", nullable = false)
    private boolean isPinned;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Builder
    public Notice(String title, String content, Boolean isPinned){
        this.title = title;
        this.content = content;
        this.isPinned = (isPinned != null) ? isPinned : false;
    }

    // 상단 고정변경
    public void updatePinned(boolean isPinned) {
        this.isPinned = isPinned;
    }

    // 공지사항 수정
    public void update(String title, String content, Boolean isPinned) {
        this.title = title;
        this.content = content;
        if (isPinned != null) {
            this.isPinned = isPinned;
        }
    }


}
