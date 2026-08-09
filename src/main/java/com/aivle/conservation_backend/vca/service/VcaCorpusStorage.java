package com.aivle.conservation_backend.vca.service;

import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfCollectionResponse;
import com.aivle.conservation_backend.vca.dto.VcaCorpusPdfResponse;
import com.aivle.conservation_backend.vca.exception.VcaApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;

// RAG용 PDF 코퍼스(corpusRoot 디렉터리)를 로컬 파일시스템에 관리한다.
// VcaService의 getCorpusPdfs/uploadCorpusPdf/deleteCorpusPdf가 위임하는 대상.
@Component
public class VcaCorpusStorage {

    private static final String PDF_CONTENT_TYPE = "application/pdf";
    private static final byte[] PDF_HEADER = new byte[]{'%', 'P', 'D', 'F', '-'};

    private final Path corpusRoot;

    public VcaCorpusStorage(
            @Value("${vca.storage.document-corpus-root}") String documentCorpusRoot
    ) {
        this.corpusRoot = Path.of(documentCorpusRoot).toAbsolutePath().normalize();
    }

    // corpusRoot 바로 아래에 있는 .pdf 파일 목록을 파일명 순으로 반환.
    VcaCorpusPdfCollectionResponse listPdfs() {
        if (!Files.exists(corpusRoot, LinkOption.NOFOLLOW_LINKS)) {
            return new VcaCorpusPdfCollectionResponse(List.of());
        }
        if (!Files.isDirectory(corpusRoot, LinkOption.NOFOLLOW_LINKS)) {
            throw storageFailure("CORPUS_STORAGE_READ_FAILED", "Failed to read the VCA PDF corpus.");
        }
        try (var paths = Files.list(corpusRoot)) {
            List<VcaCorpusPdfResponse> items = paths
                    .filter(this::isRootPdfFile)
                    .map(this::toResponse)
                    .sorted(Comparator.comparing(VcaCorpusPdfResponse::fileName))
                    .toList();
            return new VcaCorpusPdfCollectionResponse(items);
        } catch (IOException exception) {
            throw storageFailure("CORPUS_STORAGE_READ_FAILED", "Failed to read the VCA PDF corpus.");
        }
    }

    // 새 PDF를 임시 파일로 받아 %PDF- 헤더/sha256을 검증한 뒤 코퍼스에 원자적으로 추가.
    VcaCorpusPdfResponse storePdf(MultipartFile file) {
        String fileName = validateUpload(file);
        Path targetFile = corpusFile(fileName);
        Path temporaryFile = null;
        try {
            Files.createDirectories(corpusRoot);
            if (Files.exists(targetFile, LinkOption.NOFOLLOW_LINKS)) {
                throw duplicateFile();
            }
            temporaryFile = Files.createTempFile(corpusRoot, ".vca-corpus-", ".tmp");
            String sha256 = copyVerifiedPdf(file, temporaryFile);
            // 위쪽의 exists() 검사는 빠른 경로일 뿐이며, 같은 이름으로 동시에 업로드가 들어오면
            // 레이스가 발생할 수 있다. 실제 중복 방지는 moveWithoutOverwrite가 담당하는데,
            // REPLACE_EXISTING 없이 Files.move를 호출하면 대상이 이미 있을 때 원자적으로 실패하기 때문이다.
            moveWithoutOverwrite(temporaryFile, targetFile);
            return toResponse(targetFile, sha256);
        } catch (FileAlreadyExistsException exception) {
            throw duplicateFile();
        } catch (IOException exception) {
            throw storageFailure("CORPUS_STORAGE_WRITE_FAILED", "Failed to store the VCA PDF corpus file.");
        } finally {
            deleteTemporaryFile(temporaryFile);
        }
    }

    // 코퍼스에서 지정한 이름의 PDF 파일을 삭제. 파일명은 corpusFile에서 basename으로 재검증됨.
    void deletePdf(String fileName) {
        Path targetFile = corpusFile(validateFileName(fileName));
        if (!Files.isRegularFile(targetFile, LinkOption.NOFOLLOW_LINKS)) {
            throw fileNotFound();
        }
        try {
            Files.delete(targetFile);
        } catch (java.nio.file.NoSuchFileException exception) {
            throw fileNotFound();
        } catch (IOException exception) {
            throw storageFailure("CORPUS_STORAGE_DELETE_FAILED", "Failed to delete the VCA PDF corpus file.");
        }
    }

    private boolean isRootPdfFile(Path file) {
        String fileName = file.getFileName().toString();
        return Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) && fileName.endsWith(".pdf");
    }

    private VcaCorpusPdfResponse toResponse(Path file) {
        try {
            return toResponse(file, sha256(file));
        } catch (IOException exception) {
            throw storageFailure("CORPUS_STORAGE_READ_FAILED", "Failed to read the VCA PDF corpus file.");
        }
    }

    private VcaCorpusPdfResponse toResponse(Path file, String sha256) throws IOException {
        return new VcaCorpusPdfResponse(
                file.getFileName().toString(),
                PDF_CONTENT_TYPE,
                Files.size(file),
                sha256,
                Files.getLastModifiedTime(file, LinkOption.NOFOLLOW_LINKS).toInstant()
        );
    }

    private String validateUpload(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw validationError("A non-empty VCA PDF file is required.");
        }
        if (!PDF_CONTENT_TYPE.equals(file.getContentType())) {
            throw validationError("VCA PDF contentType must be application/pdf.");
        }
        return validateFileName(file.getOriginalFilename());
    }

    // 파일명에 경로 구분자/제어 문자가 없는 안전한 basename인지 검증(경로 조작 방지).
    private String validateFileName(String fileName) {
        if (fileName == null || fileName.isBlank() || fileName.contains("/") || fileName.contains("\\")
                || fileName.length() <= ".pdf".length() || !fileName.endsWith(".pdf")
                || fileName.chars().anyMatch(Character::isISOControl)) {
            throw validationError("The VCA PDF file name must be a safe basename ending in .pdf.");
        }
        return fileName;
    }

    // corpusRoot 하위 실제 파일 경로로 변환하면서, normalize 후에도 corpusRoot 바로 아래에
    // 있는지 재확인한다(파일명에 "../" 등이 섞여 경로를 벗어나는 것을 막기 위함).
    private Path corpusFile(String fileName) {
        Path targetFile = corpusRoot.resolve(fileName).normalize();
        if (!targetFile.getParent().equals(corpusRoot)) {
            throw validationError("The VCA PDF file name must be a safe basename ending in .pdf.");
        }
        return targetFile;
    }

    // 업로드 스트림을 임시 파일로 복사하면서 %PDF- 헤더를 검증하고 sha256을 함께 계산한다.
    private static String copyVerifiedPdf(MultipartFile file, Path temporaryFile) throws IOException {
        try (BufferedInputStream input = new BufferedInputStream(file.getInputStream());
             OutputStream output = Files.newOutputStream(temporaryFile)) {
            input.mark(PDF_HEADER.length);
            byte[] header = input.readNBytes(PDF_HEADER.length);
            input.reset();
            if (!startsWithPdfHeader(header)) {
                throw validationError("VCA PDF bytes must start with %PDF-.");
            }
            MessageDigest digest = sha256Digest();
            try (InputStream digestInput = new DigestInputStream(input, digest)) {
                digestInput.transferTo(output);
            }
            return HexFormat.of().formatHex(digest.digest());
        }
    }

    private static boolean startsWithPdfHeader(byte[] header) {
        if (header.length != PDF_HEADER.length) {
            return false;
        }
        for (int index = 0; index < PDF_HEADER.length; index++) {
            if (header[index] != PDF_HEADER[index]) {
                return false;
            }
        }
        return true;
    }

    private static void moveWithoutOverwrite(Path temporaryFile, Path targetFile) throws IOException {
        Files.move(temporaryFile, targetFile);
    }

    private static String sha256(Path file) throws IOException {
        MessageDigest digest = sha256Digest();
        try (InputStream input = new DigestInputStream(Files.newInputStream(file), digest)) {
            input.transferTo(OutputStream.nullOutputStream());
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static MessageDigest sha256Digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 digest is unavailable.", exception);
        }
    }

    private static void deleteTemporaryFile(Path temporaryFile) {
        if (temporaryFile == null) {
            return;
        }
        try {
            Files.deleteIfExists(temporaryFile);
        } catch (IOException exception) {
            throw storageFailure("CORPUS_STORAGE_WRITE_FAILED", "Failed to clean up the VCA PDF corpus upload.");
        }
    }

    private static VcaApiException validationError(String message) {
        return new VcaApiException(HttpStatus.BAD_REQUEST, "VALIDATION_ERROR", message);
    }

    private static VcaApiException duplicateFile() {
        return new VcaApiException(
                HttpStatus.CONFLICT,
                "CORPUS_PDF_ALREADY_EXISTS",
                "A VCA PDF corpus file with the same name already exists."
        );
    }

    private static VcaApiException fileNotFound() {
        return new VcaApiException(
                HttpStatus.NOT_FOUND,
                "CORPUS_PDF_NOT_FOUND",
                "The requested VCA PDF corpus file was not found."
        );
    }

    private static VcaApiException storageFailure(String code, String message) {
        return new VcaApiException(HttpStatus.INTERNAL_SERVER_ERROR, code, message);
    }

}
