package com.aivle.conservation_backend.user.dto;

import com.aivle.conservation_backend.user.domain.Post;
import lombok.Getter;
import org.springframework.data.domain.Page;

import java.util.List;

@Getter
public class PostPageResponse {

    private final List<PostListResponse> posts;
    private final int page;
    private final int size;
    private final long totalElements;
    private final int totalPages;
    private final boolean first;
    private final boolean last;

    public PostPageResponse(Page<Post> postPage) {
        this.posts = postPage.getContent()
                .stream()
                .map(PostListResponse::new)
                .toList();

        this.page = postPage.getNumber();
        this.size = postPage.getSize();
        this.totalElements = postPage.getTotalElements();
        this.totalPages = postPage.getTotalPages();
        this.first = postPage.isFirst();
        this.last = postPage.isLast();
    }
}