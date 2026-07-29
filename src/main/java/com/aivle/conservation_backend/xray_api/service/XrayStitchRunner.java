package com.aivle.conservation_backend.xray_api.service;

import com.aivle.conservation_backend.xray_api.client.XrayStitchClient;
import com.aivle.conservation_backend.xray_api.dto.UploadedFile;
import com.aivle.conservation_backend.xray_api.dto.XrayStitchResponse;

import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.function.Consumer;

/**
 * 결합 실행만 담당하는 별도 빈.
 *
 * @Async 는 스프링 프록시로 동작한다. 같은 클래스 안에서
 * 호출하면(self-invocation) 프록시를 타지 않아 동기로 실행되고,
 * 그러면 작업 생성 요청이 결합이 끝날 때까지 막혀 버린다.
 * 그래서 실제 실행부를 XrayStitchService 에서 이 빈으로 분리했다.
 *
 * 상태 반영은 콜백으로 넘겨받아, 상태 소유는 서비스 쪽에 남긴다.
 * 이 빈이 서비스를 직접 주입받으면 순환 참조가 된다.
 */
@Component
public class XrayStitchRunner {

    private final XrayStitchClient xrayStitchClient;

    public XrayStitchRunner(XrayStitchClient xrayStitchClient) {
        this.xrayStitchClient = xrayStitchClient;
    }

    /**
     * AI 서비스를 백그라운드 스레드에서 동기 호출한다.
     *
     * 예외를 밖으로 던지지 않는다. 백그라운드 스레드에서 던진
     * 예외는 아무도 받지 않아 작업이 RUNNING 상태로 영원히
     * 남기 때문이다. 반드시 콜백으로 실패를 알린다.
     */
    @Async("xrayStitchExecutor")
    public void run(
            List<UploadedFile> xrayFiles,
            List<UploadedFile> colorFiles,
            String artifactId,
            String configName,
            Consumer<XrayStitchResponse> onSuccess,
            Consumer<String> onFailure
    ) {
        try {
            XrayStitchResponse result = xrayStitchClient.stitch(
                    xrayFiles, colorFiles, artifactId, configName
            );

            if (result == null) {
                onFailure.accept(
                        "AI 서비스가 빈 응답을 반환했습니다."
                );
                return;
            }

            onSuccess.accept(result);

        } catch (Exception e) {
            onFailure.accept(describe(e));
        }
    }

    /**
     * 실패 원인을 사람이 읽을 수 있는 문장으로 만든다.
     *
     * 결합 실패 원인은 다양하다. 엔진 없음, 매핑에 등록되지 않은
     * 유물 ID, 설정 파일 누락, 제한 시간 초과 등이다. 원문을
     * 그대로 남겨야 프론트에서 원인을 알 수 있다.
     */
    private String describe(Exception e) {
        if (e instanceof org.springframework.web.client
                .RestClientResponseException re) {

            return "AI 서비스 오류 "
                    + re.getStatusCode().value()
                    + ": "
                    + re.getResponseBodyAsString();
        }

        if (e instanceof org.springframework.web.client
                .ResourceAccessException) {

            return "AI 서비스에 연결하지 못했습니다. "
                    + "컨테이너 상태와 xray.ai.base-url 을 "
                    + "확인하십시오. ("
                    + e.getMessage() + ")";
        }

        return e.getClass().getSimpleName() + ": " + e.getMessage();
    }
}
