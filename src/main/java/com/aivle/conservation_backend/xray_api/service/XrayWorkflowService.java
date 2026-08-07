package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayAnomalyClient;
import com.aivle.conservation_backend.xray_api.domain.XrayDefect;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectOriginType;
import com.aivle.conservation_backend.xray_api.domain.XrayDefectReviewDecision;
import com.aivle.conservation_backend.xray_api.domain.XrayJob;
import com.aivle.conservation_backend.xray_api.domain.XrayJobStatus;
import com.aivle.conservation_backend.xray_api.dto.AnalysisTarget;
import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayDefectMappingResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayDetectionResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.CompleteResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectItem;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectListResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectReviewRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.DefectReviewUpdate;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportGenerateRequest;
import com.aivle.conservation_backend.xray_api.dto.XrayWorkflowDtos.ReportTextResponse;
import com.aivle.conservation_backend.xray_api.repository.XrayDefectRepository;
import com.aivle.conservation_backend.xray_api.repository.XrayJobRepository;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Keys;
import com.aivle.conservation_backend.xray_api.storage.XrayS3Service;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

import javax.imageio.ImageIO;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

@Service
public class XrayWorkflowService {

    private final XrayJobRepository jobRepository;
    private final XrayDefectRepository defectRepository;
    private final XrayStitchService stitchService;
    private final XrayAnomalyClient anomalyClient;
    private final XrayDefectMappingService mappingService;
    private final XrayS3Service s3Service;
    private final ObjectMapper objectMapper;

    public XrayWorkflowService(
            XrayJobRepository jobRepository,
            XrayDefectRepository defectRepository,
            XrayStitchService stitchService,
            XrayAnomalyClient anomalyClient,
            XrayDefectMappingService mappingService,
            XrayS3Service s3Service,
            ObjectMapper objectMapper
    ) {
        this.jobRepository = jobRepository;
        this.defectRepository = defectRepository;
        this.stitchService = stitchService;
        this.anomalyClient = anomalyClient;
        this.mappingService = mappingService;
        this.s3Service = s3Service;
        this.objectMapper = objectMapper;
    }

    public DefectListResponse detect(String jobIdValue, Double confidence) {
        XrayJob job = requireJob(jobIdValue);
        if (job.getStatus() != XrayJobStatus.STITCHED
                && job.getStatus() != XrayJobStatus.FAILED) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "Defect detection cannot start from status: " + job.getStatus()
            );
        }
        stitchService.requireFinalizedJob(jobIdValue);
        job.markDetecting();
        jobRepository.save(job);

        try {
            List<String> fileNames = stitchService.getOrderedXraySourceFileNames(jobIdValue);
            List<String> urls = stitchService.getOrderedXraySourceUrls(jobIdValue);
            List<Integer> sourceIndexes = IntStream.range(0, fileNames.size()).boxed().toList();

            XrayDetectionResponse fragments = anomalyClient.detectBatchUrls(
                    fileNames,
                    urls,
                    sourceIndexes,
                    AnalysisTarget.FRAGMENT,
                    confidence
            );
            XrayDetectionResponse assembled = anomalyClient.detectUrl(
                    "assembled_xray.final.png",
                    stitchService.getFinalAssembledUrl(jobIdValue),
                    AnalysisTarget.ASSEMBLED,
                    confidence,
                    null
            );
            XrayDefectMappingResponse mapping = mappingService.mapDefects(
                    jobIdValue,
                    new XrayDefectMappingRequest(fragments, assembled)
            );

            List<XrayDefect> defects = buildDefects(job, assembled, mapping);
            defectRepository.deleteAllByXrayJob_Id(job.getId());
            defectRepository.saveAll(defects);

            job.markReviewReady();
            jobRepository.save(job);
            return response(job, defectRepository.findAllByXrayJob_IdOrderByIdAsc(job.getId()));
        } catch (RuntimeException e) {
            job.markFailed("Defect detection/mapping failed: " + e.getMessage());
            jobRepository.save(job);
            throw e;
        }
    }

    @Transactional(readOnly = true)
    public DefectListResponse getDefects(String jobIdValue) {
        XrayJob job = requireJob(jobIdValue);
        if (job.getStatus() != XrayJobStatus.REVIEW_READY
                && job.getStatus() != XrayJobStatus.COMPLETED) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "X-ray defects are not ready: " + job.getStatus()
            );
        }
        return response(job, defectRepository.findAllByXrayJob_IdOrderByIdAsc(job.getId()));
    }

    @Transactional
    public DefectListResponse updateDefects(String jobIdValue, DefectReviewRequest request) {
        XrayJob job = requireJob(jobIdValue);
        if (job.getStatus() != XrayJobStatus.REVIEW_READY) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "Defects can be reviewed only in REVIEW_READY status."
            );
        }
        if (request == null || request.defects() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "defects are required.");
        }

        Map<Long, XrayDefect> byId = defectRepository
                .findAllByXrayJob_IdOrderByIdAsc(job.getId())
                .stream()
                .collect(Collectors.toMap(XrayDefect::getId, Function.identity()));

        for (DefectReviewUpdate update : request.defects()) {
            if (update == null || update.id() == null || update.reviewDecision() == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST,
                        "Each defect update requires id and reviewDecision."
                );
            }
            XrayDefect defect = byId.get(update.id());
            if (defect == null) {
                throw new ResponseStatusException(
                        HttpStatus.NOT_FOUND,
                        "Defect does not belong to this job: " + update.id()
                );
            }
            try {
                defect.changeReviewDecision(XrayDefectReviewDecision.valueOf(
                        update.reviewDecision().trim().toUpperCase(Locale.ROOT)
                ));
            } catch (IllegalArgumentException e) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST,
                        "reviewDecision must be DAMAGE or NORMAL."
                );
            }
        }
        defectRepository.saveAll(byId.values());
        return response(job, byId.values().stream()
                .sorted(Comparator.comparing(XrayDefect::getId))
                .toList());
    }

    @Transactional
    public ReportTextResponse generateReportText(
            String jobIdValue,
            ReportGenerateRequest request
    ) {
        XrayJob job = requireReviewReady(jobIdValue);
        List<XrayDefect> damages = defectRepository
                .findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
                        job.getId(),
                        XrayDefectReviewDecision.DAMAGE
                );

        String regions = objectMapper.writeValueAsString(
                damages.stream().map(this::toReportRegion).toList()
        );
        Resource assembled = namedResource(
                s3Service.getBytes(XrayS3Keys.finalAssembled(job.getArtifactId().toString())),
                "assembled_xray.final.png"
        );
        List<Resource> fragments = stitchService.getOrderedXraySourceResources(jobIdValue);
        List<Resource> colors = List.of(stitchService.getColorReferenceResource(jobIdValue));
        String raw = anomalyClient.generateReportResources(
                regions,
                request == null ? null : request.artifactType(),
                request == null ? null : request.material(),
                request == null ? "summary" : request.reportStyle(),
                assembled,
                fragments,
                colors
        );

        @SuppressWarnings("unchecked")
        Map<String, Object> result = objectMapper.readValue(raw, Map.class);
        String reportText = result.get("report") == null
                ? null
                : String.valueOf(result.get("report"));
        if (reportText == null || reportText.isBlank()) {
            throw new IllegalStateException("AI report response does not contain report text.");
        }

        // AI 초안은 최종 확정 전까지 DB에 저장하지 않는다.
        return new ReportTextResponse(
                job.getId().toString(),
                job.getArtifactId().toString(),
                reportText,
                job.getStatus().name()
        );
    }

    @Transactional(readOnly = true)
    public ReportTextResponse getReportText(String jobIdValue) {
        XrayJob job = requireJob(jobIdValue);
        return reportResponse(job);
    }

    @Transactional
    public ReportTextResponse saveReportText(String jobIdValue, String reportText) {
        XrayJob job = requireReviewReady(jobIdValue);
        if (reportText == null || reportText.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "reportText is required.");
        }
        job.updateReportText(reportText.trim());
        jobRepository.save(job);
        return reportResponse(job);
    }

    @Transactional
    public CompleteResponse complete(String jobIdValue) {
        XrayJob job = requireReviewReady(jobIdValue);
        if (job.getReportText() == null || job.getReportText().isBlank()) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "Final report text must be saved before completion."
            );
        }

        List<XrayDefect> damages = defectRepository
                .findAllByXrayJob_IdAndReviewDecisionOrderByIdAsc(
                        job.getId(),
                        XrayDefectReviewDecision.DAMAGE
                );
        byte[] image = createDefectResult(job, damages);
        String key = XrayS3Keys.defectResult(job.getArtifactId().toString());
        s3Service.putBytes(
                key,
                image,
                "image/png",
                Map.of(
                        "usage", "defect_result",
                        "original_name", "defect_result.png"
                )
        );
        job.markCompleted();
        jobRepository.save(job);
        return new CompleteResponse(
                job.getId().toString(),
                job.getArtifactId().toString(),
                job.getStatus().name(),
                s3Service.presignGet(key)
        );
    }

    private List<XrayDefect> buildDefects(
            XrayJob job,
            XrayDetectionResponse assembled,
            XrayDefectMappingResponse mapping
    ) {
        Map<String, XrayDetectionResponse.AnomalyRegion> assembledById =
                safeRegions(assembled).stream()
                        .filter(region -> region.regionId() != null)
                        .collect(Collectors.toMap(
                                XrayDetectionResponse.AnomalyRegion::regionId,
                                Function.identity(),
                                (first, ignored) -> first
                        ));

        List<XrayDefect> defects = new ArrayList<>();
        for (XrayDefectMappingResponse.AssembledDecision decision
                : safeList(mapping.assembledDecisions())) {
            XrayDetectionResponse.AnomalyRegion region = assembledById.get(decision.assembledRegionId());
            if (region == null || region.bbox() == null) {
                continue;
            }
            XrayDefectOriginType originType = "CONFIRMED".equals(decision.status())
                    ? XrayDefectOriginType.MATCHED
                    : XrayDefectOriginType.ASSEMBLED_ONLY;
            defects.add(XrayDefect.create(job, originType, bboxGeometry(region.bbox())));
        }
        for (XrayDefectMappingResponse.SourceOnlyGroup group
                : safeList(mapping.sourceOnlyGroups())) {
            if (group.unionBBox() != null) {
                defects.add(XrayDefect.create(
                        job,
                        XrayDefectOriginType.SOURCE_ONLY,
                        bboxGeometry(group.unionBBox())
                ));
            }
        }
        return defects;
    }

    private Map<String, Object> bboxGeometry(XrayDetectionResponse.BoundingBox bbox) {
        Map<String, Object> geometry = new LinkedHashMap<>();
        geometry.put("type", "bbox");
        geometry.put("x1", bbox.x1());
        geometry.put("y1", bbox.y1());
        geometry.put("x2", bbox.x2());
        geometry.put("y2", bbox.y2());
        return geometry;
    }

    private Map<String, Object> bboxGeometry(XrayDefectMappingResponse.BoundingBox bbox) {
        Map<String, Object> geometry = new LinkedHashMap<>();
        geometry.put("type", "bbox");
        geometry.put("x1", bbox.x1());
        geometry.put("y1", bbox.y1());
        geometry.put("x2", bbox.x2());
        geometry.put("y2", bbox.y2());
        return geometry;
    }

    private Map<String, Object> toReportRegion(XrayDefect defect) {
        Map<String, Object> region = new LinkedHashMap<>();
        region.put("regionId", "defect-" + defect.getId());
        region.put("analysisTarget", AnalysisTarget.ASSEMBLED.getValue());
        region.put("fileName", "assembled_xray.final.png");
        region.put("className", "anomaly");
        region.put("originType", defect.getOriginType().name());
        region.put("reviewDecision", defect.getReviewDecision().name());
        region.put("bbox", defect.getGeometry());
        return region;
    }

    private byte[] createDefectResult(XrayJob job, List<XrayDefect> damages) {
        try {
            BufferedImage original = ImageIO.read(new ByteArrayInputStream(
                    s3Service.getBytes(XrayS3Keys.finalAssembled(job.getArtifactId().toString()))
            ));
            if (original == null) {
                throw new IllegalStateException("Failed to decode assembled final image.");
            }
            BufferedImage output = new BufferedImage(
                    original.getWidth(),
                    original.getHeight(),
                    BufferedImage.TYPE_INT_ARGB
            );
            Graphics2D graphics = output.createGraphics();
            try {
                graphics.drawImage(original, 0, 0, null);
                graphics.setColor(Color.RED);
                graphics.setStroke(new BasicStroke(Math.max(2.0f, original.getWidth() / 1200.0f)));
                int label = 1;
                for (XrayDefect defect : damages) {
                    Map<String, Object> geometry = defect.getGeometry();
                    int x1 = (int) Math.round(number(geometry.get("x1")));
                    int y1 = (int) Math.round(number(geometry.get("y1")));
                    int x2 = (int) Math.round(number(geometry.get("x2")));
                    int y2 = (int) Math.round(number(geometry.get("y2")));
                    graphics.drawRect(x1, y1, Math.max(1, x2 - x1), Math.max(1, y2 - y1));
                    graphics.drawString(String.valueOf(label++), x1 + 4, Math.max(14, y1 + 14));
                }
            } finally {
                graphics.dispose();
            }
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            if (!ImageIO.write(output, "png", buffer)) {
                throw new IllegalStateException("PNG writer is not available.");
            }
            return buffer.toByteArray();
        } catch (IOException e) {
            throw new IllegalStateException("Failed to create defect_result.png.", e);
        }
    }

    private DefectListResponse response(XrayJob job, Iterable<XrayDefect> defects) {
        List<DefectItem> items = new ArrayList<>();
        defects.forEach(defect -> items.add(new DefectItem(
                defect.getId(),
                defect.getOriginType().name(),
                defect.getGeometry(),
                defect.getReviewDecision().name(),
                defect.getCreatedAt(),
                defect.getUpdatedAt()
        )));
        return new DefectListResponse(
                job.getId().toString(),
                job.getArtifactId().toString(),
                job.getStatus().name(),
                stitchService.getFinalAssembledUrl(job.getId().toString()),
                List.copyOf(items)
        );
    }

    private ReportTextResponse reportResponse(XrayJob job) {
        return new ReportTextResponse(
                job.getId().toString(),
                job.getArtifactId().toString(),
                job.getReportText(),
                job.getStatus().name()
        );
    }

    private XrayJob requireReviewReady(String jobIdValue) {
        XrayJob job = requireJob(jobIdValue);
        if (job.getStatus() != XrayJobStatus.REVIEW_READY) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "X-ray job must be REVIEW_READY: " + job.getStatus()
            );
        }
        return job;
    }

    private XrayJob requireJob(String jobIdValue) {
        UUID jobId;
        try {
            jobId = UUID.fromString(jobIdValue);
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "jobId must be a UUID.");
        }
        return jobRepository.findById(jobId).orElseThrow(() ->
                new ResponseStatusException(HttpStatus.NOT_FOUND, "X-ray job not found: " + jobId)
        );
    }

    private List<XrayDetectionResponse.AnomalyRegion> safeRegions(XrayDetectionResponse response) {
        return response == null || response.regions() == null ? List.of() : response.regions();
    }

    private <T> List<T> safeList(List<T> values) {
        return values == null ? List.of() : values;
    }

    private double number(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        return Double.parseDouble(String.valueOf(value));
    }

    private Resource namedResource(byte[] bytes, String fileName) {
        return new ByteArrayResource(bytes) {
            @Override
            public String getFilename() {
                return fileName;
            }
        };
    }
}
