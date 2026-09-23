# Giai đoạn 2 — Container image dùng chung và CI

**Trạng thái:** Hoàn thành ngày 22/09/2026.
**Phạm vi:** Docker image, metadata, GitHub Actions, GHCR và clean-runner verification; chưa deploy cloud.

## 1. Mục tiêu và artifact cuối

GĐ2 tạo đúng một artifact từ source GĐ1 để AWS và Azure không tự build hai bản khác nhau. Deployment phải tham chiếu digest bất biến, không dùng riêng tag `latest`.

| Thuộc tính | Giá trị đã nghiệm thu |
|---|---|
| Image | `ghcr.io/vincody/multi-cloud-deployment-failover` |
| Commit | `5b362a2321713ce8dfef45101bf37e834a21cc73` |
| GHCR digest | `sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5` |
| Platform | `linux/amd64` |
| Actions run | [35684300924](https://github.com/vincody/multi-cloud-deployment-failover/actions/runs/35684300924) |

AWS/Azure phải dùng `image@sha256:e487...` để chứng minh cùng artifact.

## 2. File đã thay đổi và lý do

| File | Thay đổi | Lý do |
|---|---|---|
| `Dockerfile` | Pin base digest, build args, OCI labels, healthcheck | Tái lập và truy vết |
| `.dockerignore` | Loại Git, venv, docs, tests, secret, file tạm | Context nhỏ, không rò dữ liệu |
| `requirements.txt` | Pin runtime versions | Build cùng commit ổn định hơn |
| `requirements-dev.txt` | Pin test versions | Local/CI nhất quán |
| `container.yml` | Test → build/push → clean pull | Tự động hóa acceptance |
| `app/main.py` | Version/SHA đúng | UI không còn `dev/unknown` |
| `.env.example` | Version đúng, SHA local để trống | Không chặn auto-detection |

Runtime trực tiếp: FastAPI 0.141.1, prometheus-client 0.26.0, Uvicorn 0.53.0.

## 3. Dockerfile chi tiết

Base `python:3.12-slim` được pin bằng manifest digest. Build target `linux/amd64` khớp EC2 x86_64 và target Azure dự kiến.

Build args:

- `APP_VERSION`: CI đặt `sha-<full SHA>`.
- `COMMIT_SHA`: full Git SHA.

Chúng trở thành ENV và OCI labels `source`, `version`, `revision`. Cloud chỉ cần override `APP_ENV` và `APP_REGION`.

Docker HEALTHCHECK gọi `/health/ready` mỗi 30 giây, timeout 3 giây, start period 5 giây, 3 lần lỗi mới unhealthy. Nó kiểm tra bên trong container; Route 53 ở GĐ6 kiểm tra từ bên ngoài.

## 4. Build local đã thực hiện

```powershell
docker build --platform linux/amd64 --build-arg APP_VERSION=v0.1.0 --build-arg COMMIT_SHA=local-phase2 -t dcs29-app:phase2 .
```

Kết quả:

| Thuộc tính | Giá trị |
|---|---|
| Local image ID | `sha256:c0eb89450704e255300db4265e418f0bda77488e02582a7a6be26064c5b3d57f` |
| OS/arch | `linux/amd64` |
| Size | khoảng 233 MB |
| OCI version | `v0.1.0` |
| OCI revision | `local-phase2` |

Chạy test container:

```powershell
docker run --detach --rm --name dcs29-phase2-test --publish 18080:8080 --env APP_ENV=local-container --env APP_REGION=local-docker dcs29-app:phase2
```

`/health/ready` và `/version` trả HTTP 200; Docker health chuyển `starting` → `healthy`; sau đó container được dừng. Local image ID khác registry digest là bình thường.

## 5. Workflow CI hoạt động như nào

### Job `test`

1. Checkout đúng commit.
2. Setup Python 3.12 và pip cache.
3. Cài dev dependencies.
4. Chạy `pytest -q`.
5. Nếu fail, không chạy container job.

### Job `container`

1. Setup Buildx.
2. Sinh OCI labels và tag `sha-<short SHA>`, `main` hoặc release tag.
3. Pull request chỉ build, không login/push.
4. Main/tag login GHCR bằng `GITHUB_TOKEN` có `packages: write`.
5. Build `linux/amd64`, truyền version/SHA.
6. Push image.
7. Xuất digest thành job output.
8. Ghi image/commit/digest/platform vào Job Summary.
9. Upload `manifest.txt`, giữ 90 ngày.

### Job `verify-published-image`

Job chạy trên runner Ubuntu sạch:

1. Login GHCR với `packages: read`.
2. Pull đúng `IMAGE@DIGEST`.
3. Chạy container với `APP_ENV=ci-clean-pull`.
4. Retry readiness tối đa 15 lần, cách 2 giây.
5. Gọi `/version` và inspect container.
6. Dừng container.

Nhờ job này, cache/image local không thể làm bài kiểm tra pass giả.

## 6. Kết quả CI thật

Run `35684300924` hoàn thành `success`:

| Job | Kết quả | Bằng chứng |
|---|---|---|
| `test` | success | 3 API tests pass |
| `container` | success | Image được push, digest được xuất |
| `verify-published-image` | success | Runner sạch pull digest và gọi runtime thành công |

Artifact `image-identity-<commit>` chứa `manifest.txt`. Digest lưu trong artifact/summary thay vì commit vào source, tránh vòng lặp “commit digest tạo ra digest mới”.

## 7. GHCR authentication

Credential Git trên máy push source được nhưng thiếu `read:packages`; pull private package local trả `permission_denied`. Đây không phải lỗi image. CI clean runner đã pull thành công bằng quyền đúng.

Cho GĐ3/GĐ4, chọn một phương án:

1. Giữ private: tạo deployment credential chỉ có `read:packages`.
2. Dùng secret/platform identity thích hợp.
3. Chuyển package public nếu nhóm chấp nhận.

Không commit token vào Compose, YAML, screenshot hoặc history. Nếu dùng token local:

```powershell
$env:GHCR_TOKEN | docker login ghcr.io --username vincody --password-stdin
docker pull ghcr.io/vincody/multi-cloud-deployment-failover@sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5
```

## 8. Vấn đề đã gặp và xử lý

| Vấn đề | Nguyên nhân | Xử lý/kết luận |
|---|---|---|
| Docker Hub DNS lỗi ban đầu | Docker Desktop chưa resolve registry | Chạy lại khi engine có mạng |
| Build vượt thời gian phiên lệnh | pip download lâu | Chạy build nền, đọc log/exit thật |
| Local GHCR pull bị từ chối | Git token thiếu `read:packages` | Dùng clean CI job permission tối thiểu |
| UI hiện `dev/unknown` | Default runtime tĩnh | Local Git detection + CI build args |

## 9. Cách kiểm tra lại

```powershell
.\.venv\Scripts\python -m pytest -q
docker image inspect dcs29-app:phase2
```

Trên GitHub, mở run, kiểm tra cả ba job xanh, Job Summary có digest và tải artifact manifest.

## 10. Bằng chứng cần lưu

- Dockerfile, .dockerignore, pinned requirements.
- Workflow source.
- URL Actions run.
- Artifact manifest và GHCR digest.
- Output local health/version/image inspect.
- Package visibility và chiến lược credential.
- Không lưu token.

## 11. Checklist nghiệm thu

- [x] Base image và dependency trực tiếp được pin.
- [x] Build Linux AMD64 thành công.
- [x] Local container ready và Docker health healthy.
- [x] Test chạy trước build.
- [x] PR không push; main/tag push image.
- [x] Digest được lưu trong summary/artifact.
- [x] Runner sạch pull digest và kiểm tra runtime.
- [x] Không có cloud secret trong workflow.

## 12. Kết luận và hướng tiếp theo

GĐ2 tạo artifact dùng chung, chưa tạo high availability. GĐ3 deploy đúng digest lên EC2 với `APP_ENV=aws-primary`. Xem [GĐ3](GIAI_DOAN_03_AWS.md).
