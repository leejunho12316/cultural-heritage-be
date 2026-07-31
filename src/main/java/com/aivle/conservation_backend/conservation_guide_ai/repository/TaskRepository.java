package com.aivle.conservation_backend.conservation_guide_ai.repository;

import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TaskRepository extends JpaRepository<Task, String> {
}
