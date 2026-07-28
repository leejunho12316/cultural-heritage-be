package com.aivle.conservation_backend.pottery_inspection_ai.controller;

import com.aivle.conservation_backend.pottery_inspection_ai.client.PotteryInspectionAiClient;
import com.aivle.conservation_backend.pottery_inspection_ai.dto.PotteryInspectionResponseDto;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

@RequiredArgsConstructor
@RestController
@RequestMapping("/pottery-inspection")
public class PotteryInspectionAiController {

    private final PotteryInspectionAiClient client;

    @PostMapping(consumes = "multipart/form-data")
    public PotteryInspectionResponseDto inspect(
            @RequestParam("image") MultipartFile image,
            @RequestParam(name = "n_calls", defaultValue = "3") int nCalls,
            @RequestParam(name = "use_vlm_pattern", defaultValue = "true") boolean useVlmPattern
    ) {
        return client.inspect(image, nCalls, useVlmPattern);
    }
}
