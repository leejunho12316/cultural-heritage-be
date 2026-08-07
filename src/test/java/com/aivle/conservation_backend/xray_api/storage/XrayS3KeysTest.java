package com.aivle.conservation_backend.xray_api.storage;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class XrayS3KeysTest {

    @Test
    void buildsStableArtifactScopedKeys() {
        String artifactId = "11111111-1111-1111-1111-111111111111";

        assertEquals(
                "xray/" + artifactId + "/inputs/xray/piece-01.png",
                XrayS3Keys.xrayInput(artifactId, "piece-01.png")
        );
        assertEquals(
                "xray/" + artifactId + "/outputs/assembled_xray.final.png",
                XrayS3Keys.finalAssembled(artifactId)
        );
        assertEquals(
                "xray/" + artifactId + "/outputs/provenance.final.json",
                XrayS3Keys.provenance(artifactId)
        );
    }
}
