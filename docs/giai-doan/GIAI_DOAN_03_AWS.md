# Giai đoạn 3 — Deploy AWS EC2 origin chính

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** tạo origin AWS độc lập chạy đúng GHCR digest của GĐ2 với `APP_ENV=aws-primary`. GĐ3 chỉ nghiệm thu origin trực tiếp; domain chung/TLS/failover thuộc GĐ5–GĐ6.

## 1. Đầu vào bắt buộc

- AWS account có quyền EC2, VPC, Security Group và Elastic IP.
- Region dự kiến `ap-southeast-1`.
- Image: `ghcr.io/vincody/multi-cloud-deployment-failover@sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5`.
- Nếu GHCR private: deployment token chỉ có `read:packages`.
- IP quản trị hiện tại để giới hạn SSH.
- Budget alert và tag `Project=dcs29`, `Owner=<nhóm>`.

## 2. Kiến trúc GĐ3

Internet → Security Group → Elastic IP → NGINX port 80 → container app port 8080 trong Docker network. Không mở 8080 ra Internet. HTTPS/certificate sẽ thêm ở GĐ5.

Khuyến nghị Ubuntu x86_64, public subnet có route Internet Gateway, volume EBS nhỏ đủ OS/log. Bắt đầu instance nhỏ rồi đo CPU/RAM; không chọn cấu hình quá yếu khiến benchmark chỉ phản ánh thiếu tài nguyên.

## 3. Tạo tài nguyên AWS

1. Xác nhận VPC, public subnet và route `0.0.0.0/0` tới Internet Gateway.
2. Tạo Security Group:
   - TCP 22 chỉ từ IP quản trị, hoặc dùng SSM thay SSH.
   - TCP 80 từ Internet trong GĐ3.
   - TCP 443 từ Internet để chuẩn bị GĐ5.
   - Không có inbound 8080.
3. Tạo EC2 Ubuntu x86_64 trong public subnet.
4. Gán Elastic IP và associate vào instance.
5. Gắn tags, ghi instance ID, subnet ID, SG ID, EIP allocation ID.
6. Kiểm tra Estimated monthly cost trước khi giữ instance chạy lâu.

Không commit PEM key. Nếu dùng SSH key, đặt quyền file phù hợp và lưu ngoài repository.

## 4. Cài Docker trên EC2

SSH/SSM vào máy, cập nhật package index và cài Docker Engine + Compose plugin theo tài liệu Docker Ubuntu hiện hành. Sau cài:

```bash
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

Thêm user vào nhóm docker là tùy chọn; logout/login lại mới có hiệu lực. Với máy lab, có thể dùng `sudo docker` để tránh thay đổi quyền không cần thiết.

## 5. Pull private GHCR an toàn

```bash
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io -u vincody --password-stdin
sudo docker pull ghcr.io/vincody/multi-cloud-deployment-failover@sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5
```

Nhập token qua session/secret manager, không ghi token vào Compose. Sau pull có thể logout registry nếu không cần auto-pull. Ghi lại digest từ `docker image inspect`.

## 6. Cấu hình Compose

Tạo thư mục `/opt/dcs29` chứa `compose.yaml` và `nginx.conf`. Compose có hai service:

- `app` dùng image@digest, đặt `APP_ENV=aws-primary`, `APP_REGION=ap-southeast-1`, restart `unless-stopped`, chỉ expose 8080 nội bộ.
- `nginx` publish 80, proxy tới `app:8080`, restart `unless-stopped`.

Không đặt `build: .` trên EC2; server chỉ pull artifact GĐ2. Mount NGINX config read-only. Ở GĐ3, NGINX có thể phục vụ HTTP; GĐ5 bổ sung 443/certificate.

Các header proxy tối thiểu: Host, X-Real-IP, X-Forwarded-For và X-Forwarded-Proto. Timeout phải lớn hơn request thông thường nhưng không che app treo.

## 7. Deploy và kiểm tra tại máy

```bash
cd /opt/dcs29
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail 100 app
curl -f http://127.0.0.1/health/ready
curl -f http://127.0.0.1/version
```

Kỳ vọng version/commit/fingerprint khớp artifact GĐ2; environment là `aws-primary`, region là `ap-southeast-1`.

Từ laptop:

```powershell
Invoke-WebRequest -UseBasicParsing http://AWS_ELASTIC_IP/health/ready
Invoke-WebRequest -UseBasicParsing http://AWS_ELASTIC_IP/version
```

Kiểm tra `http://AWS_ELASTIC_IP:8080` không truy cập được.

## 8. Kiểm tra restart và tính độc lập

1. Restart app container; NGINX phải phục vụ lại sau khi app ready.
2. Reboot EC2; Docker/Compose restart policy phải đưa app lên.
3. Tạm tắt kết nối/URL Azure nếu đã tồn tại; AWS vẫn trả dữ liệu.
4. So image inspect với digest GĐ2.
5. Theo dõi CPU, RAM và disk trong vài phút tải nhẹ.

## 9. Rollback và recovery

Rollback deployment bằng cách thay image@digest về digest trước, `docker compose pull` và `up -d`. Nếu NGINX lỗi, kiểm tra syntax trước reload. Nếu EC2 restart mà app không lên, kiểm tra Docker service, compose ps, logs, disk và mount.

Không terminate instance khi chưa lưu evidence. Stop EC2 vẫn tính phí EBS và có thể tính phí Elastic IP/public IPv4.

## 10. Artifact phải lưu

- Instance/SG/subnet/EIP IDs và region.
- Compose, NGINX config đã xóa secret.
- Output image inspect, compose ps, health/version.
- Screenshot Security Group chứng minh không mở 8080.
- UTC deploy time, instance type, vCPU/RAM/EBS.
- Bảng chi phí ước tính/thực tế.
- Runbook start/stop/reboot/rollback.

## 11. Checklist hoàn thành

- [ ] EC2 x86_64 và Elastic IP tồn tại.
- [ ] Security Group chỉ mở port cần thiết.
- [ ] Đúng GHCR digest được pull.
- [ ] App trả `aws-primary` và fingerprint đúng.
- [ ] 8080 không public.
- [ ] Restart container/reboot EC2 tự phục hồi.
- [ ] AWS phục vụ độc lập với Azure.
- [ ] Evidence và cost proxy đã lưu.

## 12. Kết quả mong đợi và bước tiếp theo

Kết quả cuối GĐ3 là một AWS origin ổn định, chưa phải multi-cloud failover. Sau nghiệm thu, chuyển [GĐ4 — Azure Container Apps](GIAI_DOAN_04_AZURE.md) và dùng chính digest này.
