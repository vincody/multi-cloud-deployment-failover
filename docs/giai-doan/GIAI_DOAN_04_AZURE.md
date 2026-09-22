# Giai đoạn 4 — Deploy Azure Container Apps

**Mục tiêu:** tạo Azure standby độc lập, chạy đúng image digest AWS đang dùng nhưng đặt `APP_ENV=azure-standby`.

## Việc sẽ làm

1. Tạo Resource Group, Container Apps Environment và Container App ở region phù hợp.
2. Cho phép HTTPS ingress ngoài Internet; cấu hình pull image từ GHCR.
3. Đặt `minReplicas=1` trong lúc thí nghiệm để Azure đã sẵn sàng, không cold start khi AWS hỏng.
4. Đặt `APP_ENV=azure-standby`, `APP_REGION`, version và commit giống GĐ3.
5. Gọi FQDN Azure trực tiếp và so sánh `/version` với AWS: chỉ environment/region khác; version, commit, dataset fingerprint phải giống.

## Bằng chứng đạt

- Azure FQDN trả HTTP 200 độc lập khi AWS tắt.
- Cùng image digest/dataset với AWS.

## Tiếp theo

Thực hiện [GĐ5 — Domain và HTTPS](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md).
