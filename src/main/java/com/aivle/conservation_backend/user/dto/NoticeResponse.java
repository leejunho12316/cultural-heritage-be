package com.aivle.conservation_backend.user.dto;

import com.aivle.conservation_backend.user.domain.Notice;
import lombok.Getter;

import java.time.LocalDateTime;

@Getter
public class NoticeResponse {

    private final Long id;
    private final String title;
    private final String content;
    private final boolean isPinned;
    private final LocalDateTime createdAt;

    public NoticeResponse(Notice notice) {
        this.id = notice.getId();
        this.title = notice.getTitle();
        this.content = notice.getContent();
        this.isPinned = notice.isPinned();
        this.createdAt = notice.getCreatedAt();
    }
}
