package com.aivle.conservation_backend.artifact.service;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.conservation_guide_ai.domain.Task;
import com.aivle.conservation_backend.conservation_guide_ai.repository.TaskRepository;
import com.aivle.conservation_backend.user.domain.Role;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.UUID;

@RequiredArgsConstructor
@Service
@Transactional(readOnly = true)
public class ArtifactAccessService {

    private final ArtifactRepository artifactRepository;
    private final XrayJobRepository xrayJobRepository;
    private final TaskRepository taskRepository;

    public User currentUser() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !authentication.isAuthenticated()
                || !(authentication.getPrincipal() instanceof User user)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "로그인이 필요합니다.");
        }
        return user;
    }

    public boolean isAdmin(User user) {
        return user.getRole() == Role.ADMIN;
    }

    public Artifact requireArtifact(UUID artifactId) {
        Artifact artifact = artifactRepository.findById(artifactId)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "존재하지 않는 유물입니다."
                ));
        requireOwnerOrAdmin(artifact, currentUser());
        return artifact;
    }

    public Artifact requireArtifact(String artifactId) {
        try {
            return requireArtifact(UUID.fromString(artifactId));
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "올바르지 않은 artifactId입니다.");
        }
    }

    public XrayJob requireXrayJob(String jobId) {
        UUID id;
        try {
            id = UUID.fromString(jobId);
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "올바르지 않은 X-Ray jobId입니다.");
        }
        XrayJob job = xrayJobRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "X-Ray 작업을 찾을 수 없습니다."));
        requireArtifact(job.getArtifactId());
        return job;
    }

    public Task requireTask(String taskId) {
        Task task = taskRepository.findById(taskId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "보존가이드 작업을 찾을 수 없습니다."));
        if (task.getArtifact() == null) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "유물 소유권이 연결되지 않은 작업입니다.");
        }
        requireArtifact(task.getArtifact().getId());
        return task;
    }

    public void requireOwnerOrAdmin(Artifact artifact, User user) {
        if (isAdmin(user)) return;
        if (artifact.getOwner() == null || !artifact.getOwner().getId().equals(user.getId())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "해당 유물 프로젝트에 접근할 권한이 없습니다.");
        }
    }
}
