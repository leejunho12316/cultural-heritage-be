package com.aivle.conservation_backend.xray_api.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

/**
 * 조각 결합 백그라운드 실행용 스레드 풀.
 *
 * 결합은 CPU와 메모리를 크게 쓰므로 동시 실행 수를 제한한다.
 * 동시에 여러 건이 돌면 AI 서비스 컨테이너가 메모리 부족으로
 * 죽을 수 있다.
 *
 * 큐가 가득 차면 기본 정책상 호출 스레드에서 직접 실행되어
 * 요청이 소실되지는 않는다.
 */
@Configuration
@EnableAsync
public class AsyncConfig {

    @Bean(name = "xrayStitchExecutor")
    public Executor xrayStitchExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();

        executor.setCorePoolSize(1);
        executor.setMaxPoolSize(2);
        executor.setQueueCapacity(10);
        executor.setThreadNamePrefix("xray-stitch-");
        executor.initialize();

        return executor;
    }
}
