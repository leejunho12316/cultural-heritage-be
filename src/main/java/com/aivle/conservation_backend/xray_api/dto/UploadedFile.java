package com.aivle.conservation_backend.xray_api.dto;

/**
 * 업로드 파일의 내용을 메모리에 복사해 둔 것.
 *
 * MultipartFile 을 그대로 백그라운드 스레드에 넘기면 안 된다.
 * MultipartFile 은 요청 수명에 묶여 있어, HTTP 응답이 끝나면
 * 스프링이 임시 저장소를 정리한다. 결합은 응답을 먼저 보내고
 * 백그라운드에서 진행하므로, 그 시점에는 원본 파일이 이미
 * 사라져 읽을 수 없다.
 *
 * 그래서 요청 스레드에서 미리 바이트를 읽어 이 객체로 옮긴다.
 *
 * 파일명을 함께 들고 다니는 이유는, 결합 결과의 조각별 정보가
 * 파일명을 키로 삼기 때문이다. 이름이 바뀌면 조각과 변환행렬을
 * 이을 수 없다.
 */
public record UploadedFile(
        String fileName,
        byte[] content
) {
}
