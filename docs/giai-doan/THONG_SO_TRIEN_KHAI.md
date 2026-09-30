# Thông số cần tự điền trước khi triển khai

Hướng dẫn GĐ1–GĐ8 là **mẫu triển khai**, không chứa domain, IP, digest hoặc ID của một tài khoản cụ thể. Giữ bảng này trong ghi chú cá nhân của bạn; không commit bản đã điền nếu nó chứa thông tin nội bộ. Các tên tài nguyên như `dcs29-rg`, `dcs29-aws-primary` và `dcs29-azure-standby` có thể giữ nguyên nếu chưa bị trùng.

## Bảng thông số

| Ký hiệu trong hướng dẫn | Tự lấy ở đâu | Cách dùng |
|---|---|---|
| `PATH_TO_REPO` | Đường dẫn thư mục bạn đã clone repo trên laptop | Thay trong `Set-Location` ở GĐ7; không dùng đường dẫn máy người khác |
| `GHCR_IMAGE` | GĐ2: tên image ở **Packages → Container** của repo nguồn hoặc trong Actions artifact `image-identity-*`, dạng `ghcr.io/<owner>/<package>` | Cùng một image cho AWS và Azure |
| `GHCR_OWNER` | GĐ2: tài khoản/tổ chức sở hữu package, phần `<owner>` của `GHCR_IMAGE` | Chỉ cần khi login để pull package private |
| `IMAGE_DIGEST` | GĐ2: phần 64 ký tự hex sau `sha256:` của **version package đã chọn** hoặc artifact của run tạo version đó | Cùng một digest cho AWS và Azure; không lấy digest từ version khác |
| `COMMIT_SHA` | GĐ2: commit của run đã tạo `IMAGE_DIGEST` | Đối chiếu `/version` trên hai origin; khi dùng package có sẵn, đây là commit của repo nguồn |
| `DATASET_SHA256` | GĐ1: đọc `/version` của image vừa build | Đối chiếu dữ liệu mẫu giữa hai origin; `DATASET_SHA256_PREFIX` là phần đầu cùng giá trị |
| `AWS_REGION` | GĐ3: region bạn chọn trong AWS Console, ví dụ `ap-southeast-1` | Đặt `APP_REGION` của AWS theo đúng region EC2 |
| `AZURE_REGION` | GĐ4: mã **Location** thật của Azure VM, ví dụ `indiasouthcentral` | Đặt `APP_REGION` của Azure theo đúng Location VM |
| `AWS_PUBLIC_IP` | GĐ3: Elastic IP đã associate với EC2 | A record `aws-origin`, kiểm tra SSH/HTTPS |
| `AZURE_PUBLIC_IP` | GĐ4: Standard Static Public IP của Azure VM | A record `azure-origin`, kiểm tra SSH/HTTPS |
| `YOUR_DOMAIN` | Domain bạn sở hữu và có quyền đổi nameserver, ví dụ `example.com` chỉ để minh họa | Public hosted zone Route 53; không dùng literal `YOUR_DOMAIN` |
| `AWS_ORIGIN_HOST` | Ghép `aws-origin.` + domain thật | DNS A, TLS, Blackbox |
| `AZURE_ORIGIN_HOST` | Ghép `azure-origin.` + domain thật | DNS A, TLS, Blackbox |
| `SHARED_HOST` | Ghép `app.` + domain thật | Certificate trên **cả hai VM**, rồi record failover GĐ6 |
| `HOSTED_ZONE_ID` | GĐ5: Route 53 tạo khi bạn tạo public hosted zone | Giới hạn IAM policy DNS-01 |
| `ROUTE53_NS_1` … `ROUTE53_NS_4` | GĐ5: bốn giá trị trong record NS của hosted zone **mới tạo** | Điền tại nhà đăng ký domain; không lấy từ ví dụ của người khác |
| `CERT_EMAIL` | Email thật của bạn/nhóm | Certbot gửi thông báo certificate |
| `AWS_HEALTH_CHECK_ID`, `AZURE_HEALTH_CHECK_ID` | GĐ6: Route 53 tạo sau mỗi health check | Gắn vào đúng primary/secondary record |

Ví dụ tên `aws-origin.YOUR_DOMAIN` trong các trang sau nghĩa là thay **toàn bộ** `YOUR_DOMAIN` bằng domain thật của bạn trước khi nhập vào Console, NGINX hay chạy lệnh. Tương tự, thay `AWS_PUBLIC_IP`, `AZURE_PUBLIC_IP`, `GHCR_IMAGE`, `IMAGE_DIGEST`, `AWS_REGION`, `AZURE_REGION` và mọi ký hiệu viết hoa khác bằng giá trị bạn vừa ghi. `example.com` và các IP thuộc dải `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` chỉ là ví dụ tài liệu, không dùng để triển khai.

## Thứ tự điền

1. GĐ2: chọn **package GHCR đã phát hành của repo nguồn** hoặc tự chạy CI từ repo của bạn. Ghi `GHCR_IMAGE`, digest và commit của **cùng version**; dùng đúng version ấy ở cả hai VM. Nếu GHCR package public, VM pull không cần token; repo source public **không tự động chứng minh** package public. Nếu package private, cấp quyền `read:packages` bằng phương án riêng, không lưu token trong repo.
2. GĐ3–GĐ4: chọn vùng được quota/policy cho phép, tạo hai VM và ghi hai public IP ổn định. Không dùng IP đã từng thuộc một lần triển khai khác.
3. GĐ5: đăng ký hoặc dùng domain bạn sở hữu, tạo **một** public hosted zone. Sao chép bốn NS của chính zone đó sang nhà đăng ký; sau khi delegation hoạt động mới tạo A record origin, cấp certificate và cấu hình NGINX.
4. GĐ6: tạo hai health check, ghi hai ID và gắn vào record failover đúng vai trò.
5. GĐ7: sửa ba `targets` trong `observability/prometheus.yml` thành `https://AWS_ORIGIN_HOST/health/ready`, `https://AZURE_ORIGIN_HOST/health/ready` và `https://SHARED_HOST/health/ready` **sau khi đã thay các ký hiệu bằng hostname thật**; kiểm tra `docker compose config -q` trước khi chạy.

Không dùng output của một lần chạy cũ để nghiệm thu lần chạy mới. Ở mỗi giai đoạn, lưu ID, digest, thời gian UTC và kết quả kiểm tra mới vào sổ riêng; che token, private key, subscription/account ID khi chia sẻ ảnh.
