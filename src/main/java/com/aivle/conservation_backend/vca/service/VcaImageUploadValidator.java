package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.http.HttpStatus;
import org.springframework.web.multipart.MultipartFile;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Path;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.regex.Pattern;

final class VcaImageUploadValidator {

    private static final Pattern IMAGE_CONTENT_TYPE =
            Pattern.compile("^image/(jpeg|png|webp|tiff?)$");
    private static final int IMAGE_HEADER_SIZE = 12;

    private VcaImageUploadValidator() {
    }

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

    static InputStream verifiedImageInput(MultipartFile file) throws IOException {
        BufferedInputStream input = new BufferedInputStream(file.getInputStream());
        input.mark(IMAGE_HEADER_SIZE);
        byte[] header = input.readNBytes(IMAGE_HEADER_SIZE);
        input.reset();
        if (!matchesContentType(file.getContentType(), header)) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "VCA image bytes do not match the declared contentType."
            );
        }
        return input;
    }

    static String uploadSha256(MultipartFile file) throws IOException, NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = verifiedImageInput(file);
             InputStream digestInput = new DigestInputStream(input, digest)) {
            digestInput.transferTo(OutputStream.nullOutputStream());
        }
        return HexFormat.of().formatHex(digest.digest());
    }

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
