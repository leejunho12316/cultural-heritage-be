package com.aivle.conservation_backend.user.controller;

import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.dto.AddNoticeRequest;
import com.aivle.conservation_backend.user.dto.NoticeResponse;
import com.aivle.conservation_backend.user.dto.UpdateNoticeRequest;
import com.aivle.conservation_backend.user.service.NoticeService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RequiredArgsConstructor
@RestController
@RequestMapping("/api/notices")
public class NoticeApiController {

    private final NoticeService noticeService;

    @PostMapping
    public ResponseEntity<NoticeResponse> addNotice(
            @Valid @RequestBody AddNoticeRequest request,
            @AuthenticationPrincipal User currentUser) {

        NoticeResponse response = noticeService.save(request, currentUser);

        return ResponseEntity
                .status(HttpStatus.CREATED)
                .body(response);
    }

    @GetMapping
    public ResponseEntity<List<NoticeResponse>> findAllNotices() {
        return ResponseEntity.ok(noticeService.findAll());
    }

    @GetMapping("/{id}")
    public ResponseEntity<NoticeResponse> findNotice(
            @PathVariable Long id) {

        return ResponseEntity.ok(noticeService.findById(id));
    }

    @PutMapping("/{id}")
    public ResponseEntity<NoticeResponse> updateNotice(
            @PathVariable Long id,
            @RequestBody UpdateNoticeRequest request) {

        return ResponseEntity.ok(noticeService.update(id, request));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteNotice(
            @PathVariable Long id) {

        noticeService.delete(id);
        return ResponseEntity.ok().build();
    }
}