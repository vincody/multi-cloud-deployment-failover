# Lộ trình thực hiện đề tài #29

Đề tài triển khai cùng một ứng dụng ở hai cloud độc lập: AWS là môi trường chính, Azure là môi trường dự phòng. Route 53 sẽ chuyển DNS khi AWS không còn healthy.

| Giai đoạn | Trạng thái | Mục tiêu |
|---|---|---|
| [GĐ1](GIAI_DOAN_01_UNG_DUNG_LOCAL.md) | Hoàn thành | Ứng dụng demo, API health/status, metrics và test local |
| [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md) | Hoàn thành | Build một image Linux AMD64, đẩy GHCR theo commit digest |
| [GĐ3](GIAI_DOAN_03_AWS.md) | Chưa bắt đầu | Deploy image lên AWS EC2 |
| [GĐ4](GIAI_DOAN_04_AZURE.md) | Chưa bắt đầu | Deploy cùng image lên Azure Container Apps |
| [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) | Chưa bắt đầu | Một domain chung và HTTPS ở cả hai origin |
| [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) | Chưa bắt đầu | Health check và DNS failover bằng Route 53 |
| [GĐ7](GIAI_DOAN_07_OBSERVABILITY.md) | Chưa bắt đầu | Prometheus, Grafana, Blackbox Exporter |
| [GĐ8](GIAI_DOAN_08_KIEM_THU_FAILOVER.md) | Chưa bắt đầu | k6, mô phỏng sự cố, đo và báo cáo kết quả |

Tài liệu kiến trúc và runbook đầy đủ: [hướng dẫn AWS + Azure](../../HUONG_DAN_29_AWS_AZURE_DEPLOY_VA_KIEM_THU.md).

## Quy ước chung

- Không commit secret, private key, certificate private key, file `.env`, hay cloud credential.
- Image chạy ở AWS và Azure phải cùng **digest**, không dùng tag `latest` làm bằng chứng.
- Mỗi giai đoạn chỉ chuyển sang giai đoạn sau sau khi đạt tiêu chí nghiệm thu trong file tương ứng.
- Lưu lệnh đã chạy, ảnh chụp cấu hình và số đo vào `experiments/` để phục vụ báo cáo/cuối kỳ.
