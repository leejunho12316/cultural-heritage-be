package com.aivle.conservation_backend.user.repository;

import com.aivle.conservation_backend.user.domain.Notice;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface NoticeRepository extends JpaRepository<Notice, Long> {
    List<Notice> findAllByOrderByIsPinnedDescIdDesc();
}
