package com.aivle.conservation_backend.vca.dto;

// name만 받아 새 유물을 만든다. artifact_id(UUID)는 서버가 생성해 응답에 담아 돌려준다 -
// 클라이언트는 그 UUID를 받은 뒤에만 /{artifactId}/... 하위 경로에 접근할 수 있다.
public record CreateArtifactRequest(String name) {
}
