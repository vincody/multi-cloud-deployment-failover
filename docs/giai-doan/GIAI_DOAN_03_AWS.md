# Giai đoạn 3 — Deploy AWS EC2

**Mục tiêu:** tạo AWS origin độc lập chạy image digest từ GĐ2, với `APP_ENV=aws-primary`.

## Việc sẽ làm

1. Chọn region AWS (đề xuất `ap-southeast-1`), EC2 Ubuntu x86_64 nhỏ, Elastic IP và security group tối thiểu.
2. Cài Docker/Compose, pull image digest từ GHCR và chạy app port nội bộ 8080.
3. Dùng NGINX reverse proxy tại 80/443; GĐ5 mới hoàn thiện certificate.
4. Kiểm tra từ máy ngoài: `/health/ready`, `/version`, `/api/status` phải trả `aws-primary`.
5. Lưu public IP/origin hostname, instance ID, image digest và UTC time vào run manifest.

## Bằng chứng đạt

- Origin AWS trả HTTP 200 ổn định.
- `/version` khớp digest/commit của GĐ2.
- App không phụ thuộc Azure hay database riêng bên ngoài.

## Tiếp theo

Thực hiện [GĐ4 — Azure Container Apps](GIAI_DOAN_04_AZURE.md) với cùng image digest.
