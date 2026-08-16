package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.domain.Notice;
import com.aivle.conservation_backend.user.dto.AddNoticeRequest;
import com.aivle.conservation_backend.user.dto.NoticeResponse;
import com.aivle.conservation_backend.user.dto.UpdateNoticeRequest;
import com.aivle.conservation_backend.user.repository.NoticeRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class NoticeService {

    private final NoticeRepository noticeRepository;

    @Transactional
    public NoticeResponse save(AddNoticeRequest request, User currentUser) {
        Notice notice = Notice.builder()
                .title(request.getTitle())
                .content(request.getContent())
                .isPinned(request.getIsPinned())
                .author(currentUser)
                .build();

        Notice savedNotice = noticeRepository.save(notice);

        return new NoticeResponse(savedNotice);
    }

    public List<NoticeResponse> findAll() {
        return noticeRepository.findAllByOrderByIsPinnedDescIdDesc()
                .stream()
                .map(NoticeResponse::new)
                .toList();
    }

    public NoticeResponse findById(Long id) {
        Notice notice = noticeRepository.findById(id)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "존재하지 않는 공지사항입니다. id=" + id
                        )
                );

        return new NoticeResponse(notice);
    }

    @Transactional
    public NoticeResponse update(Long id, UpdateNoticeRequest request) {
        Notice notice = noticeRepository.findById(id)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "존재하지 않는 공지사항입니다. id=" + id
                        )
                );

        notice.update(
                request.getTitle(),
                request.getContent(),
                request.getIsPinned()
        );

        return new NoticeResponse(notice);
    }

    @Transactional
    public void delete(Long id) {
        Notice notice = noticeRepository.findById(id)
                .orElseThrow(() ->
                        new ResponseStatusException(
                                HttpStatus.NOT_FOUND,
                                "존재하지 않는 공지사항입니다. id=" + id
                        )
                );

        noticeRepository.delete(notice);
    }
}