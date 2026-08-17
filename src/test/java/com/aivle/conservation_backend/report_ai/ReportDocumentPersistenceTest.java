package com.aivle.conservation_backend.report_ai;

import com.aivle.conservation_backend.artifact.domain.Artifact;
import com.aivle.conservation_backend.artifact.repository.ArtifactRepository;
import com.aivle.conservation_backend.photo.service.S3PhotoStorageService;
import com.aivle.conservation_backend.report_ai.client.ReportAiClient;
import com.aivle.conservation_backend.report_ai.dto.ReportDocumentResponseDto;
import com.aivle.conservation_backend.report_ai.dto.SaveReportRequestDto;
import com.aivle.conservation_backend.report_ai.service.ReportDocumentService;
import com.aivle.conservation_backend.user.domain.Role;
import com.aivle.conservation_backend.user.domain.User;
import com.aivle.conservation_backend.user.repository.UserRepository;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.TestPropertySource;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * ArtifactSourceAdapter/ReportDocumentService의 나머지 테스트는 전부
 * Mockito로 리포지토리 자체를 목킹했다 - 실제 SQL이 한 번도 안 나갔다는
 * 뜻이다. 이 테스트는 그 갭을 메운다: 진짜 Spring 컨텍스트 + 진짜(내장
 * H2, Postgres 호환 모드) DB로 Artifact -&gt; ReportDocument 저장/조회를
 * 끝까지 돌려서, JPA 매핑(FK, jsonb report_json 컬럼)과 리포지토리 쿼리
 * 메서드가 실제 SQL 레벨에서 맞는지 확인한다.
 *
 * report-ai(LLM/문서 변환)와 S3 업로드는 이 저장소만으로는 재현할 수
 * 없는 외부 의존성이라 목으로 대체한다 - report-ai 파이프라인 자체는
 * 이전에 실데이터로 이미 검증됐고(README 참고), S3는 로컬에 AWS
 * 자격증명이 없어 실제 업로드가 불가능하다. 이 테스트가 보장하는 것은
 * "DB에 실제로 잘 저장되고 다시 읽힌다"이지 AWS/LLM 연동 자체는 아니다.
 */
@SpringBootTest
@ActiveProfiles("test")
@TestPropertySource(properties = "spring.jpa.hibernate.ddl-auto=create-drop")
@Transactional
class ReportDocumentPersistenceTest {

    @Autowired
    private ArtifactRepository artifactRepository;

    @Autowired
    private ReportDocumentService reportDocumentService;

    @Autowired
    private UserRepository userRepository;

    @MockitoBean
    private ReportAiClient reportAiClient;

    @MockitoBean
    private S3PhotoStorageService photoStorageService;

    @Test
    void 유물을_저장하고_그_id로_보고서를_저장한_뒤_실제_DB에서_다시_조회된다() {
        // 1. artifacts.user_id는 운영 RDS에서 NOT NULL이므로 테스트 유물도 실제 owner를 저장한다.
        User owner = userRepository.save(User.builder()
                .loginId("report-persistence-user")
                .email("report-persistence@example.com")
                .password("test-password")
                .nickname("report tester")
                .role(Role.USER)
                .build());

        Artifact artifact = artifactRepository.save(
                Artifact.builder()
                        .owner(owner)
                        .name("청자상감운학문매병")
                        .category("도자기")
                        .material("청자")
                        .era("고려")
                        .weight("1.2kg")
                        .bondingArea("동체 하단")
                        .treatmentPurpose("전시 보존")
                        .build()
        );
        assertThat(artifact.getId()).isNotNull();

        // 2. report-ai(.docx 변환)와 S3 업로드만 목킹 - 나머지는 전부 실제 빈/DB를 탄다.
        byte[] fakeDocx = "fake-docx-bytes".getBytes();
        when(reportAiClient.reportToDocx(any())).thenReturn(fakeDocx);
        when(photoStorageService.uploadReportDocx(eq(artifact.getId()), any()))
                .thenReturn("artifacts/" + artifact.getId() + "/reports/test.docx");
        when(photoStorageService.presignedUrl(anyString()))
                .thenReturn("https://s3.example.com/presigned-test");

        Map<String, Object> reportJson = Map.of(
                "report_type", "ceramic_treatment_report",
                "artifact_id", artifact.getId().toString(),
                "sections", java.util.List.of(Map.of("key", "header", "title", "유물 기본정보"))
        );

        // 3. 저장 - ReportDocumentRepository.save()가 실제 INSERT (FK + jsonb 컬럼 포함).
        ReportDocumentResponseDto saved = reportDocumentService.save(
                artifact.getId(),
                new SaveReportRequestDto(reportJson, Map.of())
        );
        assertThat(saved.id()).isNotNull();
        assertThat(saved.artifactId()).isEqualTo(artifact.getId());

        // 4. 조회 - 방금 커밋 안 된(트랜잭션 안, but flush됨) 데이터를 실제 SELECT 쿼리로 다시 읽는다.
        ReportDocumentResponseDto found = reportDocumentService.findLatest(artifact.getId());

        assertThat(found.id()).isEqualTo(saved.id());
        // jsonb 컬럼에 저장했다가 다시 읽은 report_json이 원본과 내용이 완전히 같은지 -
        // 이게 이 테스트의 핵심 검증 포인트다(Mockito 테스트로는 절대 못 잡는 부분).
        assertThat(found.reportJson()).isEqualTo(reportJson);
        assertThat(found.docxDownloadUrl()).isEqualTo("https://s3.example.com/presigned-test");
    }

    @Test
    void 존재하지_않는_유물로_저장을_시도하면_실제_리포지토리_조회_후_404를_던진다() {
        UUID randomArtifactId = UUID.randomUUID();

        assertThatThrownBy(() -> reportDocumentService.save(
                randomArtifactId,
                new SaveReportRequestDto(Map.of("sections", Map.of()), Map.of())
        )).isInstanceOf(ResponseStatusException.class);
    }
}
