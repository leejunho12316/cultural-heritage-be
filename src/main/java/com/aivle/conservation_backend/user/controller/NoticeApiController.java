package com.aivle.conservation_backend.user.controller;

import com.aivle.conservation_backend.user.domain.Notice;
import com.aivle.conservation_backend.user.dto.AddNoticeRequest;
import com.aivle.conservation_backend.user.dto.NoticeResponse;
import com.aivle.conservation_backend.user.dto.UpdateNoticeRequest;
import com.aivle.conservation_backend.user.service.NoticeService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RequiredArgsConstructor
@RestController
@RequestMapping("/api/notices")
public class NoticeApiController {

    private final NoticeService noticeService;
    @PostMapping
    public ResponseEntity<NoticeResponse> addNotice(@Valid @RequestBody AddNoticeRequest request) {
        NoticeResponse response = new NoticeResponse(noticeService.save(request));
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @GetMapping
    public ResponseEntity<List<NoticeResponse>> findAllNotices() {
        List<NoticeResponse> notices = noticeService.findAll()
                .stream()
                .map(NoticeResponse::new)
                .toList();
        return ResponseEntity.ok(notices);
    }

    @PutMapping("/{id}")
    public ResponseEntity<NoticeResponse> updateNotice(
            @PathVariable Long id,
            @RequestBody UpdateNoticeRequest request) {

        Notice updatedNotice = noticeService.update(id, request);
        return ResponseEntity.ok(new NoticeResponse(updatedNotice));
    }

    @GetMapping("/{id}")
    public ResponseEntity<NoticeResponse> findNotice(@PathVariable Long id) {
        NoticeResponse response = new NoticeResponse(noticeService.findById(id));
        return ResponseEntity.ok(response);
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteNotice(@PathVariable Long id) {
        noticeService.delete(id);
        return ResponseEntity.ok().build();
    }
}
