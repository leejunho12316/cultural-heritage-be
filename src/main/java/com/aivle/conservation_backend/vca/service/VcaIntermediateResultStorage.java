package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.IntermediateResultsResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.List;

@Component
public class VcaIntermediateResultStorage {

    private static final int PREVIEW_BYTES = 8_192;
    private static final List<StageDescriptor> STAGES = List.of(
            new StageDescriptor("preprocessing", "전처리"),
            new StageDescriptor("rough_masking", "거친 마스크"),
            new StageDescriptor("visual_cue_generation", "시각 단서"),
            new StageDescriptor("rag", "RAG 근거"),
            new StageDescriptor("prompt_generating", "프롬프트"),
            new StageDescriptor("mask_refining", "마스크 정제"),
            new StageDescriptor("anomaly_grouping", "이상 그룹핑"),
            new StageDescriptor("report_generating", "보고서 생성"),
            new StageDescriptor("result", "실행 영수증")
    );
    private static final List<String> PREVIEW_SUFFIXES = List.of(
            ".json",
            ".jsonl",
            ".txt",
            ".md",
            ".html",
            ".csv"
    );

    private final Path outputRoot;

    public VcaIntermediateResultStorage(
            @Value("${vca.storage.engine-output-root}") String outputRoot
    ) {
        this.outputRoot = Path.of(outputRoot).toAbsolutePath().normalize();
    }

    // VcaService.getIntermediateResults가 호출하는 진입점. 파이프라인 각 단계 산출물 디렉터리를
    // 순서대로 훑어, 비어있지 않은 단계만 응답에 담는다(디버깅/중간 결과 확인용 엔드포인트).
    IntermediateResultsResponse read(
            String artifactId,
            String assessmentRunId,
            String projectName
    ) {
        List<IntermediateResultsResponse.Stage> stages = STAGES.stream()
                .map(stage -> readStage(stage, projectName))
                .filter(stage -> !stage.items().isEmpty())
                .toList();
        return new IntermediateResultsResponse(
                artifactId,
                assessmentRunId,
                projectName,
                stages
        );
    }

    // 단계 하나(예: preprocessing)의 산출물 디렉터리를 재귀적으로 훑어 파일 항목 목록을 만든다.
    // 디렉터리가 없으면(아직 그 단계까지 진행 안 됐거나 실패) 빈 목록을 반환한다.
    private IntermediateResultsResponse.Stage readStage(
            StageDescriptor stage,
            String projectName
    ) {
        Path stageDirectory = outputRoot.resolve(stage.stage()).resolve(projectName).normalize();
        if (!stageDirectory.startsWith(outputRoot) || !Files.isDirectory(stageDirectory)) {
            return new IntermediateResultsResponse.Stage(stage.stage(), stage.displayName(), List.of());
        }
        try {
            Path realStageDirectory = stageDirectory.toRealPath(LinkOption.NOFOLLOW_LINKS);
            List<IntermediateResultsResponse.Item> items;
            try (var paths = Files.walk(stageDirectory)) {
                items = paths
                        .filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                        .filter(path -> isSafeChild(path, realStageDirectory))
                        .map(path -> toItem(stage.stage(), stageDirectory, path))
                        .toList();
            }
            return new IntermediateResultsResponse.Stage(stage.stage(), stage.displayName(), items);
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERMEDIATE_RESULTS_READ_FAILED",
                    "Failed to read VCA intermediate results."
            );
        }
    }

    // 심볼릭 링크가 stage 디렉터리 밖의 파일을 가리키도록 심어져 있어도 실제 경로(realPath)를
    // 풀어서 여전히 stage 디렉터리 안인지 확인한다 - 링크를 통한 임의 파일 노출을 막기 위함.
    private static boolean isSafeChild(Path path, Path realStageDirectory) {
        try {
            return path.toRealPath(LinkOption.NOFOLLOW_LINKS).startsWith(realStageDirectory);
        } catch (IOException exception) {
            return false;
        }
    }

    private IntermediateResultsResponse.Item toItem(String stage, Path stageDirectory, Path file) {
        String relativePath = stageDirectory.relativize(file).toString()
                .replace(File.separator, "/");
        try {
            return new IntermediateResultsResponse.Item(
                    relativePath,
                    file.getFileName().toString(),
                    contentType(stage, file),
                    Files.size(file),
                    preview(stage, file)
            );
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERMEDIATE_RESULTS_READ_FAILED",
                    "Failed to read VCA intermediate result metadata."
            );
        }
    }

    private static String contentType(String stage, Path file) throws IOException {
        String probed = Files.probeContentType(file);
        if (probed != null) {
            return probed;
        }
        return isPreviewable(stage, file) ? "text/plain" : "application/octet-stream";
    }

    private static String preview(String stage, Path file) throws IOException {
        if (!isPreviewable(stage, file)) {
            return null;
        }
        byte[] bytes;
        try (var input = Files.newInputStream(file)) {
            bytes = input.readNBytes(PREVIEW_BYTES);
        }
        return new String(bytes, StandardCharsets.UTF_8);
    }

    // rag/prompt_generating 단계는 확장자 조건을 만족해도 미리보기를 만들지 않는다
    // (내부 프롬프트/근거 원문이라 응답에 그대로 노출하고 싶지 않은 단계).
    private static boolean isPreviewable(String stage, Path file) {
        if ("rag".equals(stage) || "prompt_generating".equals(stage)) {
            return false;
        }
        String fileName = file.getFileName().toString().toLowerCase();
        return PREVIEW_SUFFIXES.stream().anyMatch(fileName::endsWith);
    }

    private record StageDescriptor(String stage, String displayName) {
    }
}
