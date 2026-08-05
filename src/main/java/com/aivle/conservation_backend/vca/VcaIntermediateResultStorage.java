package com.aivle.conservation_backend.vca;

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

    VcaResponses.IntermediateResults read(
            String artifactId,
            String assessmentRunId,
            String projectName
    ) {
        List<VcaResponses.IntermediateStage> stages = STAGES.stream()
                .map(stage -> readStage(stage, projectName))
                .filter(stage -> !stage.items().isEmpty())
                .toList();
        return new VcaResponses.IntermediateResults(
                artifactId,
                assessmentRunId,
                projectName,
                stages
        );
    }

    private VcaResponses.IntermediateStage readStage(
            StageDescriptor stage,
            String projectName
    ) {
        Path stageDirectory = outputRoot.resolve(stage.stage()).resolve(projectName).normalize();
        if (!stageDirectory.startsWith(outputRoot) || !Files.isDirectory(stageDirectory)) {
            return new VcaResponses.IntermediateStage(stage.stage(), stage.displayName(), List.of());
        }
        try {
            Path realStageDirectory = stageDirectory.toRealPath(LinkOption.NOFOLLOW_LINKS);
            List<VcaResponses.IntermediateItem> items;
            try (var paths = Files.walk(stageDirectory)) {
                items = paths
                        .filter(path -> Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
                        .filter(path -> isSafeChild(path, realStageDirectory))
                        .map(path -> toItem(stageDirectory, path))
                        .toList();
            }
            return new VcaResponses.IntermediateStage(stage.stage(), stage.displayName(), items);
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERMEDIATE_RESULTS_READ_FAILED",
                    "Failed to read VCA intermediate results."
            );
        }
    }

    private static boolean isSafeChild(Path path, Path realStageDirectory) {
        try {
            return path.toRealPath(LinkOption.NOFOLLOW_LINKS).startsWith(realStageDirectory);
        } catch (IOException exception) {
            return false;
        }
    }

    private VcaResponses.IntermediateItem toItem(Path stageDirectory, Path file) {
        String relativePath = stageDirectory.relativize(file).toString()
                .replace(File.separator, "/");
        try {
            return new VcaResponses.IntermediateItem(
                    relativePath,
                    file.getFileName().toString(),
                    contentType(file),
                    Files.size(file),
                    preview(file)
            );
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "INTERMEDIATE_RESULTS_READ_FAILED",
                    "Failed to read VCA intermediate result metadata."
            );
        }
    }

    private static String contentType(Path file) throws IOException {
        String probed = Files.probeContentType(file);
        if (probed != null) {
            return probed;
        }
        return isPreviewable(file) ? "text/plain" : "application/octet-stream";
    }

    private static String preview(Path file) throws IOException {
        if (!isPreviewable(file)) {
            return null;
        }
        byte[] bytes = Files.readAllBytes(file);
        int length = Math.min(bytes.length, PREVIEW_BYTES);
        return new String(bytes, 0, length, StandardCharsets.UTF_8);
    }

    private static boolean isPreviewable(Path file) {
        String fileName = file.getFileName().toString().toLowerCase();
        return PREVIEW_SUFFIXES.stream().anyMatch(fileName::endsWith);
    }

    private record StageDescriptor(String stage, String displayName) {
    }
}
