package com.aivle.conservation_backend.conservation_guide_ai;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

//보존 가이드 AI 첫 실행 DTO

//record : final 데이터를 담는 클래스 빠르게 선언하기.
//@JsonProperty : 보존 가이드 AI와 통신할 때 해당 필드만 변수명을 바꿔주는 기능.
public record ConservationGuideAiStartRequest (
        @JsonProperty("task_name") String taskName,
        @JsonProperty("task_manager") String taskManager,
        @JsonProperty("relic_info") Map<String, Object> relicInfo,
        @JsonProperty("relic_photo") List<String> relicPhoto,
        List<String> flow
){

}
