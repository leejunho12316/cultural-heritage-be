package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.ReportResponse;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

// summary.overallCondition을 채우는 규칙기반+LLM 요약기. VcaService.toReport가
// 리포트를 처음 조립할 때(ensureReportReady, run당 한 번만) 호출한다 - 결과가
// assessment_report.report_json에 캐싱되므로 이후 조회는 이 클래스를 다시 안 거친다.
//
// "뭘 언급할지"는 이 클래스가 코드로 미리 정하고(findings를 concept family/서술어별로
// 집계, 도자기 detail에서 null 아닌 값만 추림), LLM한테는 "이 사실들을 자연스러운
// 한국어 문장으로 이어붙여라"는 좁은 작업만 맡긴다 - LLM이 "뭐가 중요한 사실인지"까지
// 판단하게 하면 데이터에 없는 내용을 창작하는 사례가 실측으로 확인됐다(로컬 qwen2.5
// 프로토타이핑 중 "pit flaking surface"를 "곰팡이"로 의역한 사례 등).
//
// concept family/descriptor 한글 라벨은 FE의 visualVcaLabels.js
// (CONCEPT_FAMILY_LABELS/DESCRIPTOR_TERM_LABELS, 국립문화재연구소 보존과학
// 용어집 근거)와 VcaReportPdfRenderer의 라벨을 그대로 옮겼다 - 세 곳이
// 값을 맞춰 유지해야 한다.
//
// 네트워크 실패/모델 미배포/타임아웃 등 어떤 이유로든 실패하면 조용히 null을
// 돌려준다 - overallCondition은 표시 전용 보조 필드라, 이것 때문에 리포트
// 전체 생성이 실패하면 안 된다(getSystemInfo와 같은 원칙).
final class VcaOverallConditionGenerator {

    private static final Logger log = LoggerFactory.getLogger(VcaOverallConditionGenerator.class);

    // enabled=false일 때만 쓰는 자리표시자 - 이때는 generate()가 네트워크
    // 호출 전에 바로 null을 반환하므로 실제로 이 주소로 나가는 요청은 없다.
    private static final String PLACEHOLDER_BASE_URL = "http://localhost:11434";
    // vca-ai/Ollama 둘 다 이 배포에서 고정된 하나뿐이라(RunPod 팟 하나에 같이 뜸)
    // 모델을 환경변수로 바꿔 쓸 일이 없다 - 바꿀 일이 생기면 그때 다시 설정 가능하게 하면 된다.
    private static final String MODEL = "qwen2.5:14b-instruct";
    // Ollama가 OLLAMA_KEEP_ALIVE(5분) 동안 안 쓰이면 모델을 언로드한다 -
    // VCA report 완료는 보통 그보다 뜸해서 거의 매번 콜드 로드를 새로 타는데,
    // 실측(2026-08-17, RunPod RTX 4090, qwen2.5:14b-instruct)으로 로드
    // ~15초 + 생성 시간까지 합쳐 45~46초가 걸렸다. 예전 30초 타임아웃은
    // 이보다 짧아 거의 항상 중간에 끊겼다(ngrok을 통해 이상한
    // application/octet-stream 응답으로 관측됨) - 여유를 두고 120초로 둔다.
    // 이 호출은 run 완료 시점 폴링 흐름 안에서 백그라운드로 실행되므로
    // (VcaService.syncRunWithAi), 길게 기다려도 사용자 요청을 막지 않는다.
    private static final Duration TIMEOUT = Duration.ofSeconds(120);

    private static final Map<String, String> CONCEPT_FAMILY_LABELS = Map.ofEntries(
            Map.entry("crack", "균열"), Map.entry("deposit", "침전물"), Map.entry("corrosion", "부식"),
            Map.entry("biological_growth", "생물학적 오염"), Map.entry("surface_loss", "결손"),
            Map.entry("flaking", "박리"), Map.entry("stain_discoloration", "변색"),
            Map.entry("hole_pit", "구멍·천공"), Map.entry("deformation", "변형"),
            Map.entry("adhesive_residue", "접착제 잔류물"), Map.entry("unknown_visual_anomaly", "시각적 이상"),
            Map.entry("unknown", "미분류")
    );

    private static final Map<String, String> DESCRIPTOR_TERM_LABELS = Map.ofEntries(
            Map.entry("white", "흰색"), Map.entry("black", "검은색"), Map.entry("green", "녹색"),
            Map.entry("reddish", "적색"), Map.entry("yellow", "황색"), Map.entry("gray", "회색"),
            Map.entry("line", "선형"), Map.entry("spot", "반점"), Map.entry("hole", "구멍"),
            Map.entry("pit", "천공"), Map.entry("crust", "피각"), Map.entry("powder", "분말"),
            Map.entry("flaking", "박리"), Map.entry("broad", "광범위"), Map.entry("powdery", "분상"),
            Map.entry("crystalline", "결정질"), Map.entry("rough", "거친"), Map.entry("layered", "층상"),
            Map.entry("smooth", "매끄러운"), Map.entry("micro", "미세"), Map.entry("local", "국부적"),
            Map.entry("irregular", "불규칙한"), Map.entry("texture", "질감"), Map.entry("shape", "형태"),
            Map.entry("shapes", "형태"), Map.entry("patch", "반점"), Map.entry("patchy", "반점형"),
            Map.entry("patches", "반점"), Map.entry("dark", "어두운"), Map.entry("horizontal", "수평"),
            Map.entry("linear", "선형"), Map.entry("streak", "줄무늬"), Map.entry("stone", "석재"),
            Map.entry("metal", "금속"), Map.entry("ceramic", "도자기"), Map.entry("paint", "채색"),
            Map.entry("wood", "목재"), Map.entry("plaster", "회반죽"), Map.entry("textile", "직물"),
            Map.entry("glass", "유리"), Map.entry("surface", "표면"), Map.entry("artifact", "유물"),
            Map.entry("area", "부위"), Map.entry("region", "영역"), Map.entry("localized", "국부적")
    );

    private static final String SYSTEM_PROMPT = """
            당신은 문화재 보존 리포트를 요약하는 도우미입니다. \
            반드시 한국어만 사용하세요. 다른 언어를 절대 섞지 마세요. \
            familyLabel, descriptorLabel, potteryFacts의 키/값은 이미 확정된 한국어 용어이니 \
            그대로 인용하고, 새로운 단어로 바꾸거나 의역하지 마세요. \
            제공된 JSON에 없는 내용은 추측하지 마세요. \
            다른 설명이나 사고 과정 없이 요약 문단만 출력하세요.""";

    private final RestClient restClient;
    private final boolean enabled;

    VcaOverallConditionGenerator() {
        this(System.getenv("VCA_AI_BASE_URL"), Boolean.parseBoolean(System.getenv("VCA_AI_OLLAMA_ENABLED")));
    }

    // Ollama는 vca-ai와 같은 RunPod 팟에서 vca-ai의 /ollama 프록시 경로로만
    // 열려 있다(무료 ngrok이 고정 도메인을 하나만 줘서 별도 터널이 안 됨) -
    // 그래서 vca-ai용으로 이미 있는 base-url을 그대로 재사용하고 별도
    // OLLAMA_BASE_URL을 안 둔다. VCA_AI_OLLAMA_ENABLED만 명시적으로 켠
    // 환경(런팟)에서만 시도한다 - 안 켠 로컬 개발/테스트에서까지 매번 연결
    // 타임아웃을 기다리지 않도록.
    VcaOverallConditionGenerator(String vcaAiBaseUrl, boolean ollamaEnabled) {
        this.enabled = ollamaEnabled && vcaAiBaseUrl != null && !vcaAiBaseUrl.isBlank();
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        requestFactory.setReadTimeout(TIMEOUT);
        this.restClient = RestClient.builder()
                .baseUrl(enabled ? vcaAiBaseUrl.replaceAll("/+$", "") + "/ollama" : PLACEHOLDER_BASE_URL)
                .requestFactory(requestFactory)
                .build();
    }

    // findings + 도자기 검사 결과로부터 overallCondition 한 문단을 생성한다.
    // OLLAMA_BASE_URL이 설정 안 됐거나 실패하면(네트워크/타임아웃/모델 없음 등)
    // null을 돌려준다.
    String generate(List<ReportResponse.Finding> findings, ReportResponse.PotteryInspection pottery) {
        if (!enabled) {
            return null;
        }
        try {
            ObjectMapper mapper = new ObjectMapper();
            ObjectNode structuredFacts = buildStructuredFacts(mapper, findings, pottery);
            String userPrompt = buildUserPrompt(structuredFacts);
            return callOllama(mapper, userPrompt);
        } catch (RuntimeException error) {
            log.warn("VCA overallCondition generation failed, leaving it null", error);
            return null;
        }
    }

    private ObjectNode buildStructuredFacts(
            ObjectMapper mapper,
            List<ReportResponse.Finding> findings,
            ReportResponse.PotteryInspection pottery
    ) {
        ObjectNode root = mapper.createObjectNode();
        var findingsNode = root.putArray("findings");
        for (ReportResponse.Finding finding : findings) {
            ObjectNode findingNode = findingsNode.addObject();
            findingNode.put("familyLabel", conceptFamilyLabel(finding.conceptFamily()));
            findingNode.put("descriptorLabel", translateDescriptor(finding.descriptor()));
            var citationsNode = findingNode.putArray("citationSources");
            if (finding.citations() != null) {
                for (ReportResponse.Citation citation : finding.citations()) {
                    if (citation.sourceCitation() != null) {
                        citationsNode.add(citation.sourceCitation());
                    }
                }
            }
        }

        ObjectNode potteryFacts = root.putObject("potteryFacts");
        boolean patternFailed = false;
        boolean humanReviewRecommended = false;
        if (pottery != null) {
            humanReviewRecommended = pottery.humanReviewRecommended();
            Map<String, Object> detail = pottery.detail();
            if (detail != null) {
                putIfPresent(potteryFacts, "시대 후보", detailField(detail, "era", "prediction"));
                putIfPresent(potteryFacts, "표면 광택 수준", detailField(detail, "glaze", "prediction"));
                putIfPresent(potteryFacts, "외형 완전성", detailField(detail, "completeness", "prediction"));
                patternFailed = detailField(detail, "pattern_era_color", "error") != null;
            }
        }
        root.put("patternAnalysisFailed", patternFailed);
        root.put("humanReviewRecommended", humanReviewRecommended);
        return root;
    }

    @SuppressWarnings("unchecked")
    private static Object detailField(Map<String, Object> detail, String key, String subKey) {
        Object section = detail.get(key);
        if (!(section instanceof Map<?, ?> sectionMap)) {
            return null;
        }
        return ((Map<String, Object>) sectionMap).get(subKey);
    }

    private static void putIfPresent(ObjectNode node, String key, Object value) {
        if (value != null) {
            node.put(key, String.valueOf(value));
        }
    }

    private static String conceptFamilyLabel(String conceptFamily) {
        if (conceptFamily == null) {
            return "미분류";
        }
        return CONCEPT_FAMILY_LABELS.getOrDefault(conceptFamily, conceptFamily);
    }

    // FE visualVcaLabels.js의 translateDescriptor()와 동일한 단어 단위 사전 치환.
    private static String translateDescriptor(String descriptor) {
        // "unknown"은 mask_refining의 passthrough 경로(참고 문헌 근거를
        // 못 찾은 후보)에서 내려오는 값으로, DESCRIPTOR_TERM_LABELS에 없어
        // 그대로 영문으로 요약문에 섞여 나온다 - 빈 문자열과 같은 취급으로
        // 막는다.
        if (descriptor == null || descriptor.isBlank() || "unknown".equalsIgnoreCase(descriptor.trim())) {
            return "세부 정보 없음";
        }
        List<String> translated = new ArrayList<>();
        for (String term : descriptor.split("\\s+")) {
            translated.add(DESCRIPTOR_TERM_LABELS.getOrDefault(term.toLowerCase(), term));
        }
        return String.join(" ", translated);
    }

    private static String buildUserPrompt(ObjectNode structuredFacts) {
        return """
                아래 structured_facts에 있는 항목을 하나도 빠짐없이 전부 반영해 문단으로 요약하세요.
                문장 개수 제한은 없습니다 - 전부 담는 게 우선입니다.
                - findings: familyLabel + descriptorLabel을 그대로 이어서 문장화(용어를 바꾸지 말 것),
                  citationSources는 반드시 "~에 따르면" 식으로 언급(빠뜨리지 말 것)
                - potteryFacts: 각 키를 그대로 항목명으로 써서 "키: 값" 형태의 사실을 문장에 자연스럽게 녹여넣기
                - patternAnalysisFailed=true면 문양 분석 실패 명시
                - humanReviewRecommended=true면 전문가 검토 권장 문구로 마무리

                [structured_facts]
                %s
                """.formatted(structuredFacts.toPrettyString());
    }

    private String callOllama(ObjectMapper mapper, String userPrompt) {
        ObjectNode payload = mapper.createObjectNode();
        payload.put("model", MODEL);
        payload.put("stream", false);
        var messages = payload.putArray("messages");
        messages.addObject().put("role", "system").put("content", SYSTEM_PROMPT);
        messages.addObject().put("role", "user").put("content", userPrompt);
        var options = payload.putObject("options");
        options.put("temperature", 0.0);

        JsonNode response = restClient.post()
                .uri("/api/chat")
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .body(payload)
                .retrieve()
                .body(JsonNode.class);

        if (response == null) {
            return null;
        }
        JsonNode content = response.path("message").path("content");
        if (content.isMissingNode() || content.asString().isBlank()) {
            return null;
        }
        return content.asString().strip();
    }
}
