# Lộ trình thực hiện đề tài #29

Đề tài triển khai cùng một ứng dụng ở hai cloud độc lập: AWS là môi trường chính, Azure là môi trường dự phòng. Route 53 sẽ chuyển DNS khi AWS không còn healthy.

**Bắt đầu tại [bảng thông số triển khai](THONG_SO_TRIEN_KHAI.md).** Các trang GĐ1–GĐ8 dùng ký hiệu như `YOUR_DOMAIN`, `AWS_PUBLIC_IP`, `GHCR_IMAGE` và `IMAGE_DIGEST`; mỗi người tự thay bằng giá trị của tài khoản, domain và lần build của mình. Không chạy lệnh hoặc lưu cấu hình với ký hiệu mẫu còn nguyên.

| Giai đoạn | Điều kiện chuyển bước | Mục tiêu |
|---|---|---|
| [GĐ1](GIAI_DOAN_01_UNG_DUNG_LOCAL.md) | UI/API và test local pass trên máy bạn | Ứng dụng demo, API health/status, metrics và test local |
| [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md) | Actions run của bạn pass, ghi image/digest/commit | Build một image Linux AMD64, đẩy GHCR theo commit digest |
| [GĐ3](GIAI_DOAN_03_AWS_CONSOLE.md) | AWS origin HTTP/health/version pass, reboot pass | Deploy image lên AWS EC2 |
| [GĐ4](GIAI_DOAN_04_AZURE.md) | Azure origin pass, cùng digest với AWS, reboot pass | Deploy ứng dụng lên Azure Ubuntu VM |
| [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) | DNS origin, HTTPS/TLS và renewal pass trên hai VM | Một domain chung và HTTPS ở cả hai origin |
| [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) | Hai health check healthy và quan sát failover/failback | Health check và DNS failover bằng Route 53 |
| [GĐ7](GIAI_DOAN_07_OBSERVABILITY.md) | Ba probe URL của domain bạn có dữ liệu trên Grafana | Prometheus, Grafana, Blackbox Exporter |
| [GĐ8](GIAI_DOAN_08_KIEM_THU_FAILOVER.md) | Lưu số đo/lượt thử và giải thích kết quả | k6, mô phỏng sự cố, đo và báo cáo kết quả |

Kiến trúc và workflow: [sơ đồ](../../images/ARCHITECTURE.md). Thao tác triển khai: [AWS Console](GIAI_DOAN_03_AWS_CONSOLE.md) và [Azure Portal](GIAI_DOAN_04_AZURE_PORTAL.md). [Trang hướng dẫn tổng hợp](../../HUONG_DAN_29_AWS_AZURE_DEPLOY_VA_KIEM_THU.md) dẫn về lộ trình hiện hành.

Ghi chú và ảnh từ một lượt triển khai cũ trong `experiments/` chỉ là bằng chứng tham khảo; không dùng IP, region hoặc digest trong đó cho lần chạy mới.

## Quy ước chung

- Không commit secret, private key, certificate private key, file `.env`, hay cloud credential.
- Image chạy ở AWS và Azure phải cùng **digest**, không dùng tag `latest` làm bằng chứng.
- Mỗi giai đoạn chỉ chuyển sang giai đoạn sau sau khi đạt tiêu chí nghiệm thu trong file tương ứng.
- Lưu lệnh đã chạy, ảnh chụp cấu hình và số đo vào `experiments/` để phục vụ báo cáo/cuối kỳ.
