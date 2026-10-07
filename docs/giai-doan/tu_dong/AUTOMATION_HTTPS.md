# Automation HTTPS trong Terraform

HTTPS được triển khai qua `terraform_data.https`, bật bằng `enable_https=true` sau khi public DNS đã delegate tới hosted zone. Không chạy cloud apply/cấp certificate thật trong quá trình phát triển này.

## 1. Cấu hình

Trong `deploy/terraform/terraform.tfvars`:

```hcl
enable_https = true
certificate_email = "EMAIL_THAT_CUA_BAN"
azure_dns_credentials_file = "C:/Users/YOUR_USER/.ssh/dcs29-azure-dns.ini"
```

Phải thay email/path bằng giá trị thật; giữ `enable_workload_automation=true`. Lượt đầu giữ HTTPS false, tạo hạ tầng/app rồi đổi NS tại nhà đăng ký. Các bước domain, IAM Console, nội dung file credential, plan/apply và kiểm tra đều trình bày ngay tại [mục 8 của hướng dẫn triển khai](TRIEN_KHAI_BANG_TERRAFORM.md#8-tự-động-domain-certificate-ingress-https-và-renewal).

AWS dùng IAM instance role. Azure dùng access key của IAM user DNS-only Terraform đã tạo, từ file INI `[default]` ngoài repo; key được đọc bởi Python, chuyển qua SSH stdin và giữ trên Azure root-only. Terraform chỉ lưu path, không tạo access key hoặc đọc nội dung credential vào state. Không dùng credential có session token hết hạn cho renewal tự động.

## 2. Trình tự

1. Chờ SSH; kiểm tra K3s/app và CRD Middleware có sẵn, không tự start K3s đang bị guard chặn.
2. Cài Certbot, DNS Route 53 plugin và công cụ kiểm tra trên hai VM.
3. Kiểm tra NS/A bằng hai resolver công khai, dừng nếu delegation/IP chưa đúng.
4. Ghi credential trên Azure nếu được cung cấp; không gửi key này tới EC2.
5. Dừng hai renewal timer, chờ renewal service đang chạy kết thúc.
6. Cấp certificate lần lượt AWS rồi Azure: mỗi certificate có origin riêng và `app.DOMAIN`; retry không force-renew certificate phù hợp còn hạn.
7. Tạo `issues-tls`, HTTPS Ingress, Middleware và redirect HTTP cho hai hostname.
8. Kiểm tra TLS trust/SNI thực tế và `/version` qua localhost từng VM; kiểm tra redirect. Ghi `/opt/dcs29/https-result.json`.
9. Cố bật lại renewal timer trên cả hai VM dù một phase thất bại; apply báo lỗi nếu cấu hình/kiểm tra không hoàn tất.

Không tạo certificate bằng Terraform ACME resource nên private key TLS không nằm trong state. Certbot giữ key/certificate trên VM và hook cập nhật Kubernetes Secret. Secret phải được bảo vệ bằng quyền quản trị; base64 không phải mã hóa.

## 3. Renewal và giới hạn

- AWS chạy 00:00/12:00 UTC; Azure 06:00/18:00 UTC.
- Timer không random/catch-up sau boot; renewal service giới hạn 45 phút. VM phải chạy và đồng hồ đúng; máy tắt không gia hạn.
- Hook chỉ xử lý lineage tương ứng `dcs29-aws`/`dcs29-azure`, cập nhật TLS Secret khi Certbot gia hạn thành công. Ingress/Traefik nhận Secret cập nhật.
- Lịch riêng tránh các lượt bình thường cùng sửa TXT của hostname chung; không có distributed lock. Không chạy Certbot thủ công đồng thời hai bên, theo dõi journal để phát hiện lỗi renewal.
- `https_verify` kiểm tra VM-local routing/SNI/trust; user vẫn kiểm tra HTTPS từ laptop để chứng minh public IP/firewall/DNS hoạt động.

Record `app` chưa cần tồn tại để DNS-01 cấp certificate; kiểm tra hostname chung trước DNS failover bằng `curl --resolve` tới từng IP. Health check phụ thuộc HTTPS resource khi bật. Writer check Azure vẫn 503 khi standby, không phải lỗi certificate.

## 4. File và retry

| File | Vai trò |
|---|---|
| `deploy/terraform/https.tf` | Biến, trigger, provisioner, output HTTPS |
| `deploy/terraform/https/deploy.py` | Đọc credential file, điều phối hai VM và xử lý lỗi |
| `deploy/terraform/https/remote.py` | Certbot, Ingress, TLS probe, systemd timer/hook |
| `/etc/letsencrypt/live/dcs29-SITE` | Certificate và TLS private key |
| `/root/.aws/credentials` Azure | IAM credential phục vụ DNS-01/renewal, quyền 0600 |
| `/etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s` | Cập nhật Secret sau renewal |
| `/etc/systemd/system/certbot.timer.d/10-dcs29-schedule.conf` | Lịch riêng từng site |
| `/etc/systemd/system/certbot.service.d/10-dcs29-renewal.conf` | Timeout và credential profile Azure |
| `/opt/dcs29/https-ingress.json`, `https-result.json` | Manifest và kết quả kiểm tra, không chứa key |

Đổi path/email/domain, workload resource hoặc HTTPS bundle sẽ chạy lại orchestration. Đổi nội dung file credential **cùng path** không tạo trigger; khi rotate key hoặc cần retry, từ `deploy/terraform`:

```powershell
terraform plan -replace='terraform_data.https[0]' -out=https-retry.tfplan
terraform apply https-retry.tfplan
```

Đọc plan để tránh replace VM/disk ngoài dự kiến. Không xóa state/Secrets hoặc force-renew liên tục để sửa lỗi DNS. Chưa có rollback TLS tự động sau mọi lỗi; giữ certificate cũ, xem log phase và retry sau sửa nguyên nhân.

`enable_https=false` không gỡ cấu hình đã chạy trong VM; nó chỉ ngừng quản lý resource orchestration. Terraform vẫn chưa giám sát drift certificate/timer liên tục. Sau VM tắt dài ngày, xác nhận writer/guard, kiểm tra hạn certificate và chạy lại HTTPS nếu cần.

## 5. Kiểm thử

Đã kiểm tra Terraform fmt/validate, mock-provider tests cho opt-in và thiếu email/workloads; Python tests cho thứ tự, failure recovery, credential, Ingress, timer và SSH stdin. Không gọi production/staging ACME hoặc thay đổi cloud tài khoản người dùng.

Nghiệm thu thực tế còn cần delegation domain, quyền DNS, ACME issuance/renewal, K3s/Traefik Secret update và HTTPS từ Internet trên hai VM.

Nguồn: [Certbot DNS Route 53](https://certbot-dns-route53.readthedocs.io/en/stable/), [Certbot renewal/deploy hooks](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates), [Traefik Ingress](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress/).

[Quay lại nhánh tự động](README.md).
