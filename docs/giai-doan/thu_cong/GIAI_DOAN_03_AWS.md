# Giai đoạn 3 — Deploy AWS EC2 origin chính

**Đầu vào:** điền `AWS_REGION`, `GHCR_IMAGE`, `IMAGE_DIGEST` trong [bảng thông số](THONG_SO_TRIEN_KHAI.md).
**Mục tiêu:** tạo t3.medium (2 vCPU/4 GiB), Ubuntu 24.04 x86_64, gp3 30 GiB ban đầu và K3s độc lập. Sau GĐ4 tạo origin AWS chạy đúng GHCR digest của GĐ2 với `APP_ENV=aws`. GĐ3 chỉ nghiệm thu origin trực tiếp; domain chung/TLS/failover thuộc GĐ5–GĐ6.

**Hướng dẫn thao tác từng bước:** [AWS Console → EC2 → K3s/PostgreSQL/Issues → kiểm tra nghiệm thu](GIAI_DOAN_03_AWS_CONSOLE.md).

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Nắm các mốc triển khai AWS; dùng Console guide để thực hiện từng lệnh. |
| Chuẩn bị | Đã ghi region, image và digest trong bảng thông số. |
| Hoàn tất khi | Origin AWS hoạt động; kiểm tra theo checklist. |

**Luồng thao tác:** [1. Đầu vào bắt buộc](#1-đầu-vào-bắt-buộc) → [3. Tạo tài nguyên AWS](#3-tạo-tài-nguyên-aws) → [4. Cài K3s và kiểm tra — SSH EC2](#4-cài-k3s-và-kiểm-tra--ssh-ec2) → [6. PostgreSQL, schema và app](#6-postgresql-schema-và-app) → [7. Kiểm tra origin](#7-kiểm-tra-origin) → [11. Checklist](#11-checklist).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Đầu vào bắt buộc

- AWS account có quyền EC2, VPC, Security Group và Elastic IP.
- Region dự kiến `AWS_REGION`.
- Image: `GHCR_IMAGE@sha256:IMAGE_DIGEST`.
- Nếu GHCR private: deployment token chỉ có `read:packages`.
- IP quản trị hiện tại để giới hạn SSH.
- Budget alert và tag `Project=dcs29`, `Owner=<nhóm>`.

## 3. Tạo tài nguyên AWS

1. Xác nhận VPC, public subnet và route `0.0.0.0/0` tới Internet Gateway.
2. Tạo Security Group:
   - TCP 22 chỉ từ IP quản trị, hoặc dùng SSM thay SSH.
   - TCP 80 từ Internet trong GĐ3.
   - TCP 443 chỉ mở khi cấu hình HTTPS ở GĐ5.
   - Không có inbound 8080.
3. Tạo EC2 Ubuntu x86_64 trong public subnet.
4. Gán Elastic IP và associate vào instance.
5. Gắn tags, ghi instance ID, subnet ID, SG ID, EIP allocation ID.
6. Kiểm tra Estimated monthly cost trước khi giữ instance chạy lâu.

Không commit PEM key. Nếu dùng SSH key, đặt quyền file phù hợp và lưu ngoài repository.

## 4. Cài K3s và kiểm tra — SSH EC2

Theo [AWS Console mục 7–8](GIAI_DOAN_03_AWS_CONSOLE.md): SSH, uname/nproc/free/df, cài K3S_VERSION đã chốt, đợi service active/node Ready. K3s có containerd; không cần Docker Compose.

## 5. Chốt image và namespace

Theo mục 9 của cùng guide: tạo /opt/dcs29, namespace issues; chốt GHCR image/digest. Public K3s tự pull; private cần imagePullSecret, không chỉ docker login host.

## 6. PostgreSQL, schema và app

Theo [AWS Console mục 9](GIAI_DOAN_03_AWS_CONSOLE.md#9-chuẩn-bị-namespace-và-image) và [Azure Portal mục 7–8](GIAI_DOAN_04_AZURE_PORTAL.md#7-namespace-mạng-riêng-và-postgresql): mạng riêng/PVC/StatefulSet → schema primary → standby Azure → Deployment/Service. Các mẫu manifest đã có ngay trong Console/Portal guide; thay IP/digest theo mạng/disk thực tế trước khi tạo file và apply.

## 7. Kiểm tra origin

Sau DB/app/Ingress: /issues.html, /version, /api/database/status, /api/issues; commit/digest đúng, AWS primary writable. /health/live không đủ chứng minh DB.

## 8. Restart/reboot và tính độc lập

Theo AWS Console mục 11: rollout restart app, reboot VM, xác nhận writer và phê duyệt startup guard trước khi start K3s, rồi kiểm tra node/pods/PVC/API. Không xóa PVC, không promote Azure vì một pod lỗi.

## 9. Rollback và recovery

Đổi/rollback app theo digest đã nghiệm thu khi schema tương thích. Sau failover AWS cũ không tự ghi lại; rejoin từ Azure trước failback ở GĐ6. Không rollback DNS sang DB cũ đã lệch dữ liệu.

## 10. Artifact

VM/AMI/type/region/SG/EIP/disk IDs; K3s version, digest, PVC, API và reboot; không secret. Ghi credit mode, CPU/RAM/disk và thời gian compute/IP.

## 11. Checklist

- [ ] t3.medium 2 vCPU/4 GiB, Ubuntu x86_64, gp3 30 GiB ban đầu.
- [ ] SG/SSH/EIP đúng; DB/API/NodePort không public.
- [ ] K3s node Ready sau reboot, cluster độc lập Azure.
- [ ] DB/app nghiệm thu riêng sau phần cấu hình GĐ4.

## 12. Tiếp theo

Theo [Azure Portal](GIAI_DOAN_04_AZURE_PORTAL.md) tạo Standard_B2s và cluster độc lập; domain/TLS ở GĐ5.

---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## 2. Kiến trúc GĐ3

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

Internet → EIP/SG → K3s Traefik → Service → Issues → PostgreSQL primary/PVC. Cụm Azure độc lập, standby nhận WAL qua kết nối riêng. Không dùng Compose/NGINX cũ tranh cổng K3s.

</details>
