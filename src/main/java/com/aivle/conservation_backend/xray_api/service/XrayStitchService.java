package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.dto.XrayJobResponse;
import com.aivle.conservation_backend.xray_api.dto.XrayJobStatusResponse;
import com.aivle.conservation_backend.xray_api.dto.UploadedFile;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchResponse;

import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

/**
 * 조각 결합 작업의 상태를 소유한다.
 *
 * AI 서비스는 상태를 갖지 않는다. 요청을 받아 엔진을 돌리고
 * 결과를 돌려준 뒤 임시 파일을 지운다. 따라서 작업 이력과
 * 진행 상태를 관리할 책임은 Spring 에 있다.
 *
 * 흐름
 *
 *   1. 프론트가 조각을 업로드하면 jobId 를 즉시 반환한다(PENDING)
 *   2. 백그라운드 스레드가 AI 서비스를 호출한다(RUNNING)
 *   3. 결과를 받아 상태를 갱신한다(COMPLETED | FAILED)
 *   4. 프론트는 jobId 로 상태를 폴링하고, 완료되면 결과를 받는다
 *
 * 지금은 상태를 메모리에 들고 있다. 재시작하면 사라지므로
 * 실제 서비스에서는 DB 로 옮겨야 한다. 검수 이력이 공식 기록이
 * 되려면 영속 저장이 전제다. jobs 맵을 리포지토리 호출로
 * 바꾸면 되도록 접근 지점을 한곳에 모아 두었다.
 */
@Service
public class XrayStitchService {

    private final XrayStitchRunner xrayStitchRunner;

    /** 작업 상태. 이 맵이 상태의 진실이다. */
    private final ConcurrentMap<String, XrayJobStatusResponse> jobs =
            new ConcurrentHashMap<>();

    /** 완료된 결합본 이미지 바이트. */
    private final ConcurrentMap<String, byte[]> results =
            new ConcurrentHashMap<>();

    public XrayStitchService(XrayStitchRunner xrayStitchRunner) {
        this.xrayStitchRunner = xrayStitchRunner;
    }

    // ------------------------------------------------------------
    // 작업 생성
    // ------------------------------------------------------------

    /**
     * 결합 작업을 만들고 즉시 반환한다.
     *
     * 실제 결합은 백그라운드에서 진행되므로 이 메서드는
     * 파일 크기와 무관하게 바로 끝난다.
     */
    public XrayJobResponse createJob(
            String artifactId,
            List<MultipartFile> xrayFiles,
            List<MultipartFile> colorFiles,
            String configName
    ) {
        validate(artifactId, xrayFiles);

        // 업로드 내용을 요청 스레드에서 미리 읽어 둔다.
        //
        // MultipartFile 은 요청 수명에 묶여 있다. 응답을 먼저
        // 보내고 백그라운드에서 결합을 진행하므로, 그때 읽으려
        // 하면 스프링이 이미 임시 파일을 정리한 뒤라 실패한다.
        List<UploadedFile> xrayPayload = readAll(xrayFiles);
        List<UploadedFile> colorPayload = readAll(colorFiles);

        String jobId = UUID.randomUUID().toString();

        jobs.put(jobId, XrayJobStatusResponse.of(
                jobId,
                artifactId,
                "PENDING",
                "결합 작업이 접수되었습니다."
        ));

        // 별도 빈에 위임해 @Async 프록시를 태운다.
        // 여기서 직접 호출하면 동기로 실행되어 응답이 막힌다.
        xrayStitchRunner.run(
                xrayPayload,
                colorPayload,
                artifactId,
                configName,
                result -> onSuccess(jobId, artifactId, result),
                message -> onFailure(jobId, artifactId, message)
        );

        return new XrayJobResponse(
                jobId,
                artifactId,
                "PENDING",
                "결합 작업이 접수되었습니다.",
                colorPayload.size(),
                xrayPayload.size()
        );
    }

    // ------------------------------------------------------------
    // 상태 조회
    // ------------------------------------------------------------

    public XrayJobStatusResponse getJobStatus(String jobId) {
        XrayJobStatusResponse status = jobs.get(jobId);

        if (status == null) {
            throw new IllegalArgumentException(
                    "존재하지 않는 jobId 입니다: " + jobId
            );
        }

        return status;
    }

    /** 완료된 결합본 이미지. 없으면 null. */
    public byte[] getResultImage(String jobId) {
        return results.get(jobId);
    }

    // ------------------------------------------------------------
    // 콜백 - 백그라운드 스레드에서 호출된다
    // ------------------------------------------------------------

    private void onSuccess(
            String jobId,
            String artifactId,
            XrayStitchResponse result
    ) {
        String resultUrl = null;

        if (result.compositeImage() != null) {
            try {
                results.put(
                        jobId,
                        Base64.getDecoder()
                                .decode(result.compositeImage())
                );

                resultUrl = "/api/xray/stitch/jobs/"
                        + jobId + "/result";

            } catch (IllegalArgumentException e) {
                onFailure(
                        jobId,
                        artifactId,
                        "결합본 이미지를 해석하지 못했습니다: "
                                + e.getMessage()
                );
                return;
            }
        }

        jobs.put(jobId, new XrayJobStatusResponse(
                jobId,
                artifactId,
                "COMPLETED",
                "결합이 완료되었습니다.",
                null,
                resultUrl,
                result.fragments(),
                result.summary()
        ));
    }

    private void onFailure(
            String jobId,
            String artifactId,
            String errorMessage
    ) {
        jobs.put(jobId, new XrayJobStatusResponse(
                jobId,
                artifactId,
                "FAILED",
                "결합에 실패했습니다.",
                errorMessage,
                null,
                null,
                null
        ));
    }

    // ------------------------------------------------------------

    /**
     * 업로드 내용을 메모리로 옮긴다.
     *
     * 여기서 읽어 두지 않으면 백그라운드 스레드가 실행될 때
     * 원본 임시 파일이 이미 사라져 있다.
     */
    private List<UploadedFile> readAll(List<MultipartFile> files) {
        List<UploadedFile> result = new ArrayList<>();

        if (files == null) {
            return result;
        }

        for (MultipartFile file : files) {
            if (file == null || file.isEmpty()) {
                continue;
            }

            try {
                String name = file.getOriginalFilename();

                result.add(new UploadedFile(
                        name != null && !name.isBlank()
                                ? name
                                : "image.jpg",
                        file.getBytes()
                ));

            } catch (IOException e) {
                throw new IllegalArgumentException(
                        "업로드 파일을 읽을 수 없습니다: "
                                + file.getOriginalFilename()
                );
            }
        }

        return result;
    }

    private void validate(
            String artifactId,
            List<MultipartFile> xrayFiles
    ) {
        if (artifactId == null || artifactId.isBlank()) {
            throw new IllegalArgumentException(
                    "artifactId 는 필수입니다."
            );
        }

        if (xrayFiles == null || xrayFiles.isEmpty()) {
            throw new IllegalArgumentException(
                    "X-ray 조각 이미지가 최소 1장 필요합니다."
            );
        }
    }
}
