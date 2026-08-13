package com.aivle.conservation_backend.user.service;

import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.domain.Notice;
import com.aivle.conservation_backend.user.dto.AddNoticeRequest;
import com.aivle.conservation_backend.user.dto.UpdateNoticeRequest;
import com.aivle.conservation_backend.user.repository.NoticeRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class NoticeService {

    private final NoticeRepository noticeRepository;

    @Transactional
    public Notice save(AddNoticeRequest request, User currentUser){
        Notice notice = Notice.builder()
                .title(request.getTitle())
                .content(request.getContent())
                .isPinned(request.getIsPinned())
                .author(currentUser)
                .build();

        return noticeRepository.save(notice);
    }

    public List<Notice> findAll(){
        return noticeRepository.findAllByOrderByIsPinnedDescIdDesc();
    }

    public Notice findById(Long id) {
        return noticeRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 공지사항입니다. id=" + id));
    }

    @Transactional
    public Notice update(Long id, UpdateNoticeRequest request){
        Notice notice = noticeRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 공지사항입니다. id=" + id));
        notice.update(request.getTitle(),request.getContent(),request.getIsPinned());
        return notice;
    }


    @Transactional
    public void delete(Long id) {
        Notice notice = findById(id);
        noticeRepository.delete(notice);
    }
}
