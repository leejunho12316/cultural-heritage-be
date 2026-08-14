package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.http.HttpStatus;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.regex.Pattern;

// VcaS3ImageStorage(S3 업로드 경로)가 사용하는 이미지 업로드 검증 유틸.
// VcaSharedStorage(로컬 디스크 경로)에도 동일한 매직 바이트 검증 로직이 별도로 존재한다 -
// 두 저장소 구현이 독립적으로 유지되고 있어 한쪽만 고치면 다른 쪽은 반영되지 않는다.
final class VcaImageUploadValidator {

    private static final Pattern IMAGE_CONTENT_TYPE =
            Pattern.compile("^image/(jpeg|png|webp|tiff?)$");
    private static final int IMAGE_HEADER_SIZE = 12;

    private VcaImageUploadValidator() {
    }

    // 파일이 비어있지 않고 Content-Type이 허용된 이미지 포맷인지 1차 검증.
    static void validateUpload(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "A non-empty VCA image file is required."
            );
        }
        String contentType = file.getContentType();
        if (contentType == null || !IMAGE_CONTENT_TYPE.matcher(contentType).matches()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "VCA image contentType must be image/jpeg, image/png, image/webp, image/tif or image/tiff."
            );
        }
    }

    // S3 업로드 경로(VcaS3ImageStorage.storeUpload) 전용: 파일 전체를 바이트 배열로 읽어
    // 헤더 검증 + SHA-256 계산을 한 번에 끝낸다. 예전에는 헤더 확인용으로 mark(12)만 걸어둔
    // BufferedInputStream을 그대로 RequestBody.fromInputStream에 넘겼는데, AWS SDK가 전송 중
    // 재시도할 때 그 작은 되감기 한도를 넘어선 지점에서 reset()을 호출해 "Resetting to invalid
    // mark"로 깨졌다(실제 RunPod 경유 업로드에서 재현됨). 바이트 배열은 되감을 스트림 상태 자체가
    // 없어 RequestBody.fromBytes와 함께 쓰면 재시도가 항상 안전하다.
    record VerifiedImage(byte[] bytes, String sha256) {
    }

    static VerifiedImage verifyAndReadBytes(MultipartFile file) throws IOException, NoSuchAlgorithmException {
        byte[] bytes = file.getBytes();
        byte[] header = Arrays.copyOf(bytes, Math.min(bytes.length, IMAGE_HEADER_SIZE));
        if (!matchesContentType(file.getContentType(), header)) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "VCA image bytes do not match the declared contentType."
            );
        }
        String sha256 = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        return new VerifiedImage(bytes, sha256);
    }

    // 경로 구분자를 제거해 원본 파일명에서 basename만 남긴다(경로 조작 방지).
    static String safeFileName(String originalFileName) {
        if (originalFileName == null || originalFileName.isBlank()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "The VCA upload file name is required."
            );
        }
        return Path.of(originalFileName).getFileName().toString();
    }

    private static boolean matchesContentType(String contentType, byte[] header) {
        return switch (contentType) {
            case "image/jpeg" -> startsWith(header, new int[]{0xFF, 0xD8, 0xFF});
            case "image/png" -> startsWith(header, new int[]{0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A});
            case "image/webp" -> isWebp(header);
            case "image/tif", "image/tiff" -> isTiff(header);
            default -> false;
        };
    }

    private static boolean startsWith(byte[] source, int[] expected) {
        if (source.length < expected.length) {
            return false;
        }
        for (int index = 0; index < expected.length; index++) {
            if (Byte.toUnsignedInt(source[index]) != expected[index]) {
                return false;
            }
        }
        return true;
    }

    private static boolean isWebp(byte[] header) {
        return header.length >= 12
                && header[0] == 'R'
                && header[1] == 'I'
                && header[2] == 'F'
                && header[3] == 'F'
                && header[8] == 'W'
                && header[9] == 'E'
                && header[10] == 'B'
                && header[11] == 'P';
    }

    private static boolean isTiff(byte[] header) {
        return startsWith(header, new int[]{0x49, 0x49, 0x2A, 0x00})
                || startsWith(header, new int[]{0x4D, 0x4D, 0x00, 0x2A});
    }
}
