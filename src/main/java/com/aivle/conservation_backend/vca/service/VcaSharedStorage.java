package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.BufferedInputStream;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.regex.Pattern;

// VcaImageStorage(S3) 빈이 없을 때 VcaService가 폴백으로 사용하는 로컬 디스크 저장소.
// 업로드 이미지, run 입력 디렉터리를 storageRoot 아래에 직접 관리한다.
@Component
public class VcaSharedStorage {

    private static final Pattern IMAGE_CONTENT_TYPE =
            Pattern.compile("^image/(jpeg|png|webp|tiff?)$");
    private static final int IMAGE_HEADER_SIZE = 12;

    private final Path storageRoot;
    private final String containerRoot;

    public VcaSharedStorage(
            @Value("${vca.storage.local-root}") String localRoot,
            @Value("${vca.storage.container-root}") String containerRoot
    ) {
        this.storageRoot = Path.of(localRoot).toAbsolutePath().normalize();
        this.containerRoot = removeTrailingSlash(containerRoot);
    }

    // 검증된 이미지를 storageRoot/uploads/{imageId}/ 아래에 저장하며 sha256을 함께 계산.
    StoredImage storeUpload(String imageId, MultipartFile file) {
        validateUpload(file);
        String fileName = safeFileName(file.getOriginalFilename());
        Path uploadDirectory = storageRoot.resolve("uploads").resolve(imageId).normalize();
        Path targetFile = uploadDirectory.resolve(fileName).normalize();
        requireChild(targetFile, uploadDirectory, "Invalid VCA upload file path.");

        try {
            Files.createDirectories(uploadDirectory);
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = verifiedImageInput(file);
                 InputStream digestInput = new DigestInputStream(input, digest)) {
                Files.copy(digestInput, targetFile, StandardCopyOption.REPLACE_EXISTING);
            }
            return new StoredImage(
                    fileName,
                    file.getContentType(),
                    file.getSize(),
                    HexFormat.of().formatHex(digest.digest()),
                    targetFile
            );
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "UPLOAD_STORAGE_FAILED",
                    "Failed to store the uploaded VCA image."
            );
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 digest is unavailable.", exception);
        }
    }

    // 엔진이 읽을 수 있도록 업로드 이미지들을 run 전용 입력 디렉터리로 복사한다.
    // 기존 runDirectory는 매번 삭제 후 재생성하므로, 같은 assessmentRunId로 다시 호출하면
    // 이전에 준비된 입력이 사라진다(재시도/재생성 목적이 아니라면 호출에 주의).
    RunInputDirectory materializeRunInput(
            String assessmentRunId,
            List<StoredImageReference> images
    ) {
        Path runDirectory = storageRoot.resolve(assessmentRunId).normalize();
        requireChild(runDirectory, storageRoot, "Invalid VCA run directory path.");
        Path inputDirectory = runDirectory.resolve("input").normalize();
        requireChild(inputDirectory, runDirectory, "Invalid VCA run input path.");
        try {
            deleteRecursively(runDirectory);
            Files.createDirectories(inputDirectory);
            for (StoredImageReference image : images) {
                if (image.localPath() == null) {
                    throw new VcaApiException(
                            HttpStatus.CONFLICT,
                            "NOT_READY",
                            "Uploaded image bytes are required before creating a VCA assessment run."
                    );
                }
                Path targetFile = inputDirectory
                        .resolve(image.imageId() + "-" + safeFileName(image.fileName()))
                        .normalize();
                requireChild(targetFile, inputDirectory, "Invalid VCA run input file path.");
                Files.copy(image.localPath(), targetFile, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "RUN_INPUT_STORAGE_FAILED",
                    "Failed to prepare VCA assessment input images."
            );
        }
        return new RunInputDirectory(containerRoot + "/" + assessmentRunId + "/input");
    }

    // 이미지 삭제 API(VcaService.deleteImage)에서 호출되어 업로드 디렉터리 전체를 제거한다.
    void deleteUpload(String imageId, Path storedPath) {
        if (storedPath == null) {
            return;
        }
        Path uploadDirectory = storageRoot.resolve("uploads").resolve(imageId).normalize();
        Path normalizedPath = storedPath.toAbsolutePath().normalize();
        requireChild(uploadDirectory, storageRoot.resolve("uploads").normalize(),
                "Invalid VCA upload directory path.");
        if (!normalizedPath.startsWith(uploadDirectory)) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "Invalid VCA stored upload path."
            );
        }
        try {
            deleteRecursively(uploadDirectory);
        } catch (IOException exception) {
            throw new VcaApiException(
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "UPLOAD_STORAGE_DELETE_FAILED",
                    "Failed to delete the stored VCA image."
            );
        }
    }

    private static void validateUpload(MultipartFile file) {
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

    // 멀티파트 Content-Type 헤더는 클라이언트가 스스로 선언한 값이라 그대로 신뢰할 수 없으므로,
    // 아래에서 실제 파일 앞부분 바이트를 읽어 선언된 타입과 일치하는지 검증한다.
    private static InputStream verifiedImageInput(MultipartFile file) throws IOException {
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

    private static String safeFileName(String originalFileName) {
        if (originalFileName == null || originalFileName.isBlank()) {
            throw new VcaApiException(
                    HttpStatus.BAD_REQUEST,
                    "VALIDATION_ERROR",
                    "The VCA upload file name is required."
            );
        }
        return Path.of(originalFileName).getFileName().toString();
    }

    // imageId/fileName 등 외부 입력으로 만든 경로가 "../"를 통해 parent 밖으로
    // 벗어나지 않았는지 확인하는 경로 조작(path traversal) 방지 가드.
    private static void requireChild(Path path, Path parent, String message) {
        if (!path.getParent().equals(parent.normalize())) {
            throw new VcaApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", message);
        }
    }

    private static void deleteRecursively(Path directory) throws IOException {
        if (!Files.exists(directory)) {
            return;
        }
        try (var paths = Files.walk(directory)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }

    private static String removeTrailingSlash(String value) {
        String result = value;
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    record StoredImage(
            String fileName,
            String contentType,
            long sizeBytes,
            String sha256,
            Path localPath
    ) {
    }

    record StoredImageReference(String imageId, String fileName, Path localPath) {
    }

    record RunInputDirectory(String containerPath) {
    }
}
