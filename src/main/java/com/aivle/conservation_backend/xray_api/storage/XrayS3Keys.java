package com.aivle.conservation_backend.xray_api.storage;

public final class XrayS3Keys {

    private XrayS3Keys() {
    }

    public static String root(String artifactId) {
        return "xray/" + artifactId;
    }

    public static String colorPrefix(String artifactId) {
        return root(artifactId) + "/inputs/color/";
    }

    public static String xrayPrefix(String artifactId) {
        return root(artifactId) + "/inputs/xray/";
    }

    public static String colorInput(String artifactId, String fileName) {
        return colorPrefix(artifactId) + fileName;
    }

    public static String xrayInput(String artifactId, String fileName) {
        return xrayPrefix(artifactId) + fileName;
    }

    public static String assembled(String artifactId) {
        return root(artifactId) + "/outputs/assembled_xray.png";
    }

    public static String layout(String artifactId) {
        return root(artifactId) + "/outputs/layout.json";
    }

    public static String report(String artifactId) {
        return root(artifactId) + "/outputs/report.json";
    }

    public static String finalizationBundle(String artifactId) {
        return root(artifactId) + "/outputs/finalization_bundle.zip";
    }

    public static String finalLayout(String artifactId) {
        return root(artifactId) + "/outputs/layout.final.json";
    }

    public static String finalAssembled(String artifactId) {
        return root(artifactId) + "/outputs/assembled_xray.final.png";
    }

    public static String sourceOwner(String artifactId) {
        return root(artifactId) + "/outputs/source_owner.final.png";
    }

    public static String fragmentOwner(String artifactId) {
        return root(artifactId) + "/outputs/fragment_owner.final.png";
    }

    public static String seamZone(String artifactId) {
        return root(artifactId) + "/outputs/seam_zone.final.png";
    }

    public static String overlapMask(String artifactId) {
        return root(artifactId) + "/outputs/overlap_mask.final.png";
    }

    public static String provenance(String artifactId) {
        return root(artifactId) + "/outputs/provenance.final.json";
    }
}
