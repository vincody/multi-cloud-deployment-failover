# Giai đoạn 2 — Container image dùng chung và CI

**Trạng thái:** Đã triển khai ngày 22/09/2026.
**Phụ thuộc:** GĐ1 hoàn thành; Docker Desktop phải pull được `python:3.12-slim`; người thực hiện có quyền ghi vào GitHub Packages của repository.

## Mục tiêu

Tạo **một image Linux AMD64 bất biến** từ source đã kiểm tra ở GĐ1, đẩy image đó lên GitHub Container Registry (GHCR), và ghi lại `sha256` digest. AWS EC2 và Azure Container Apps ở các giai đoạn sau đều dùng chính digest này.

Đây là nguyên tắc quan trọng: tag như `latest` hoặc `v1.0.0` có thể bị ghi đè; digest xác định chính xác từng byte của image. Nhờ đó nhóm có thể chứng minh hai cloud chạy cùng application artifact.

## Kết quả đã triển khai

- Dockerfile dùng base image cố định bằng digest, dependencies trực tiếp được pin version.
- Image nhận APP_VERSION và COMMIT_SHA qua build arguments, đồng thời có OCI labels.
- Docker có HEALTHCHECK gọi /health/ready.
- .dockerignore giới hạn build context, không đưa .git, secret, tài liệu hay test output vào image.
- GitHub Actions chạy test trước, build đúng linux/amd64, rồi mới push GHCR trên main/tag.
- Pull request chỉ build kiểm tra và không push image.
- Mỗi lần push tạo tag theo commit, in digest vào Job Summary và upload manifest.txt làm artifact 90 ngày.
- Một CI job trên runner sạch đăng nhập với quyền packages:read, pull đúng digest và gọi health/version.
- Local image dcs29-app:phase2 đã build; container trả HTTP 200 cho readiness/version và chuyển sang trạng thái healthy.

Digest GHCR được lưu ở artifact của workflow thay vì commit ngược vào repository. Cách này tránh vòng lặp: commit một digest mới sẽ tạo ra một image và digest khác.

## Kết quả cần có

- Image `ghcr.io/vincody/multi-cloud-deployment-failover` build cho `linux/amd64`.
- Các tag theo commit SHA và tag release (nếu tạo release).
- Digest dạng `sha256:...` được lưu vào tài liệu run/deploy.
- GitHub Actions tự chạy test trước build/push.
- Có thể pull image về máy sạch và chạy `/health/ready` thành công.

## Các bước thực hiện

### 1. Xác nhận Docker và đăng nhập GitHub Container Registry

Sau khi Docker Desktop có mạng, kiểm tra:

```powershell
docker pull python:3.12-slim
docker login ghcr.io
```

Khi login, dùng GitHub username và Personal Access Token có quyền `write:packages` (và `read:packages`). Không chép token vào file repository, workflow hay ảnh chụp công khai.

### 2. Build và test image tại máy

```powershell
docker build --platform linux/amd64 -t dcs29-app:local .
docker run --rm -p 8080:8080 `
  -e APP_ENV=local-container `
  -e APP_REGION=local `
  -e APP_VERSION=phase2-test `
  -e COMMIT_SHA=manual-test `
  dcs29-app:local
```

Ở terminal khác:

```powershell
Invoke-WebRequest http://127.0.0.1:8080/health/ready
Invoke-WebRequest http://127.0.0.1:8080/version
```

Kỳ vọng `/version` phản ánh các biến môi trường đã truyền. Đây là cách chứng minh cùng image nhưng cấu hình runtime khác nhau giữa AWS và Azure.

### 3. Tạo workflow CI

Tạo `.github/workflows/container.yml` với các responsibility:

1. Trigger khi push `main` và pull request.
2. Setup Python, cài `requirements-dev.txt`, chạy `pytest -q`.
3. Chỉ push image khi push vào `main` hoặc khi tạo tag; pull request chỉ build kiểm tra.
4. Login GHCR bằng `GITHUB_TOKEN`, đặt `packages: write` đúng job cần push.
5. Build/push bằng Docker Buildx cho `linux/amd64`.
6. Gắn tag immutable theo `${{ github.sha }}`; có thể thêm semantic version khi release.
7. Xuất digest trong job summary.

Không đưa AWS/Azure credential vào workflow ở GĐ2. CI giai đoạn này chỉ chịu trách nhiệm tạo artifact đã kiểm tra.

### 4. Lưu artifact identity

Sau push, lấy digest từ GitHub Actions summary hoặc:

```powershell
docker buildx imagetools inspect ghcr.io/vincody/multi-cloud-deployment-failover:<commit-sha>
```

Tạo một run manifest trong `experiments/runs/` (file có thể bị `.gitignore`; chỉ commit bản mẫu đã xóa thông tin nhạy cảm) bao gồm: UTC time, commit, image name, digest, platform, người build và kết quả test.

Lưu ý xác thực local: credential Git hiện tại của máy push source được nhưng thiếu scope read:packages, nên không dùng nó làm bằng chứng pull GHCR. Workflow dùng GITHUB_TOKEN với quyền tối thiểu packages:read trong một job sạch để xác minh artifact đã publish.

## Tiêu chí nghiệm thu GĐ2

- [x] `docker build --platform linux/amd64` thành công.
- [x] Container local trả `/health/ready` HTTP 200.
- [x] CI chạy test trước khi build.
- [x] Image được push GHCR bằng tag commit SHA.
- [x] Ghi lại image digest `sha256:...` trong GitHub Actions Summary và artifact.
- [x] Pull image theo GHCR digest và xác nhận metadata/platform.

## Sau GĐ2

Chuyển sang [GĐ3 — AWS EC2](GIAI_DOAN_03_AWS.md). EC2 sẽ pull đúng image digest, đặt `APP_ENV=aws-primary`, và không tự build source trên server.
