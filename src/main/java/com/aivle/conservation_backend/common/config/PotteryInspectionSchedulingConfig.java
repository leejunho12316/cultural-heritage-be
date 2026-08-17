package com.aivle.conservation_backend.common.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/** 육안조사 AI job을 FE 접속 여부와 무관하게 서버에서 계속 확인한다. */
@Configuration
@EnableScheduling
public class PotteryInspectionSchedulingConfig {
}
