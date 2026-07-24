# 테스트용 로컬 이미지

강화처리 2-2 습윤효과테스트(`reinforcement_wetting_test`)는 VLM(vision 모델)이 실제로 이미지를 열어봐야 해서,
공개 URL이 없을 때는 이 폴더에 테스트용 이미지 파일을 넣고 `resume` body에 파일명만 적으면 됩니다.

예: 이 폴더에 `before.png`, `after.png`를 넣었다면

```
{
 "resume": {
  "before_photo_urls": ["test_photos/before.png"],
  "after_photo_urls": ["test_photos/after.png"]
 }
}
```

docker-compose가 이 폴더를 컨테이너의 `/code/test_photos`에 그대로 마운트하므로,
이미지 파일만 바꿔 넣으면 재빌드 없이 바로 반영됩니다 (컨테이너 재시작도 필요 없음).
