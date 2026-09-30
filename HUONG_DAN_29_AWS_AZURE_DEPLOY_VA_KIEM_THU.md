# Hướng dẫn triển khai đề tài #29

Hướng dẫn hiện hành dùng **AWS EC2 + Azure Ubuntu VM + Docker Compose + Route 53**. Để triển khai lại từ đầu bằng tài khoản và domain của bạn:

1. Ghi các giá trị của lần triển khai mới vào [bảng thông số](docs/giai-doan/THONG_SO_TRIEN_KHAI.md). Bảng giải thích cách lấy image digest, region, public IP, hosted zone ID, nameserver và health check ID.
2. Làm theo [lộ trình GĐ1–GĐ8](docs/giai-doan/README.md). Dùng [hướng dẫn AWS Console](docs/giai-doan/GIAI_DOAN_03_AWS_CONSOLE.md), [Azure Portal](docs/giai-doan/GIAI_DOAN_04_AZURE_PORTAL.md), [domain/HTTPS](docs/giai-doan/GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) và [Route 53 failover](docs/giai-doan/GIAI_DOAN_06_ROUTE53_FAILOVER.md) tại các giai đoạn tương ứng.
3. Xem [sơ đồ kiến trúc](images/ARCHITECTURE.md) để hiểu luồng DNS, monitoring và thí nghiệm.

Tài liệu cũ từng mô tả Azure Container Apps và ghi thông số của một lần triển khai riêng. Nội dung đó đã được thay bằng lộ trình trên; nếu cần đối chiếu lịch sử, xem Git history. Không dùng domain, IP, nameserver, digest, certificate hoặc credential của lần triển khai khác.
