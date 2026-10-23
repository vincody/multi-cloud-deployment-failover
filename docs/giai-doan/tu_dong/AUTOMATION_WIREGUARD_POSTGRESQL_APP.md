# Automation WireGuard, PostgreSQL và Issues

Nhánh Terraform triển khai hai origin K3s độc lập: AWS `t3.medium` primary và Azure `Standard_B2s` standby, mỗi VM 4 GiB RAM và OS disk 30 GiB. Chức năng được triển khai trong repository; chưa chạy apply hoặc nghiệm thu trên VM cloud thật.

## 1. Trình tự một lần apply

| Thứ tự | Tác vụ | Điều kiện hoàn thành |
|---|---|---|
| 1 | Tạo VM/mạng/IP, bootstrap Ubuntu/K3s phiên bản pin | SSH truy cập được, cloud-init thành công, K3s đang chạy |
| 2 | Kiểm tra database có sẵn trên cả hai VM | Không nhận DB thủ công, không đảo role đã đổi sau failover, không mở lại gate đang khóa |
| 3 | Mở UDP 51820 từ peer, cài WireGuard, sinh key trên VM | Tunnel AWS `.1`, Azure `.2`; không mở database ra public |
| 4 | Sinh password trên AWS, chuyển tới Azure qua SSH | File root-only và Secret tồn tại, retry giữ nguyên password |
| 5 | Triển khai PostgreSQL 17 AWS | PVC Bound, PV Retain, DB primary đúng major |
| 6 | Apply app AWS, chạy schema từ image | `database.issues.initialize()` hoàn tất, app Ready |
| 7 | Azure base backup và streaming replication | PVC riêng, clone có marker hoàn tất, DB standby đúng major |
| 8 | Apply app Azure và HTTP Ingress hai bên | App Ready, đúng environment và cùng commit SHA |
| 9 | Gửi issue mẫu AWS, đọc lại từ Azure | Issue/comments/history bằng nhau, Azure streaming/read-only, HTTP Ingress trả dữ liệu |
| 10 | Ghi kết quả trên VM | `/opt/dcs29/automation-result.json` chứa image, role, issue ID, PVC |

Terraform quản lý hạ tầng và một resource `terraform_data.workloads`. Resource dùng `local-exec` chạy Python chuẩn/OpenSSH trên **laptop**, còn tác vụ root thực hiện qua SSH trên VM. Không cần cài Python package cho script automation. Đây là provisioner có thứ tự và các bước kiểm tra; Terraform không theo dõi liên tục trạng thái pod/Secret hoặc drift bên trong VM.

Khi đổi VM ID/IP, app digest, cấu hình automation hoặc nội dung bundle, orchestration chạy lại. Thay cấu hình health check/DNS đơn thuần không chạy lại orchestration. Khi failover đã chuyển writer sang Azure, script initial deployment sẽ từ chối chạy; dùng quy trình vận hành theo role hiện tại.

## 2. Đầu vào trước plan

Trong `deploy/terraform/terraform.tfvars`:

```hcl
enable_workload_automation = true
ssh_private_key_path       = "C:/Users/YOUR_USER/.ssh/dcs29_terraform"
postgres_image             = "docker.io/library/postgres@sha256:DIGEST_THAT"
automation_python          = "python"
standby_storage_generation = "initial"
enable_health_checks       = false
enable_failover            = false
```

- Python 3.10+, OpenSSH trên laptop; private key phải khớp `ssh_public_key`. Script dùng BatchMode, vì vậy key có passphrase cần SSH agent đã nạp key (`ssh-add`). User cloud cần `sudo -n`.
- `postgres_image` lấy từ `postgres:17-bookworm` công khai, digest hỗ trợ linux/amd64. Init container kiểm tra major 17 và UID postgres 999. Không dùng Alpine hoặc nâng major bằng đổi digest.
- App GHCR phải public/linux/amd64, cùng digest trên hai phía, chứa module `database.issues` và schema hiện tại. Không tự tạo registry token.
- Đúng public IPv4 `/32` laptop trong `ssh_cidr`, laptop kết nối được hai VM trong toàn bộ apply. Lần đầu SSH chấp nhận host key mới; host key thay đổi sẽ bị chặn, cần kiểm chứng VM trước khi xử lý known_hosts.
- Mạng `10.200.29.0/24` không được trùng route có sẵn; pod CIDR dùng mặc định K3s `10.42.0.0/16`. Nếu dùng CIDR tùy biến, phải sửa pg_hba/automation tương ứng trước triển khai.

Chế độ chỉ tạo hạ tầng: đặt `enable_workload_automation=false`, `postgres_image=""`, `ssh_private_key_path=""` trước triển khai mới. Sau khi đã dựng replication, **giữ true**: tắt biến sẽ gỡ rule WireGuard, ảnh hưởng kết nối database.

## 3. File và dữ liệu

| File/resource | Vai trò |
|---|---|
| `deploy/terraform/automation.tf` | Rule WireGuard, trigger và local-exec |
| `automation/deploy.py` | Điều phối AWS trước Azure, kiểm tra role/API/replication |
| `automation/remote.py` | Root RPC trên VM; capture output lệnh để tránh lộ credential |
| `automation/templates/*.yaml.tmpl` | ConfigMap, PVC/StatefulSet, EndpointSlice, app và HTTP Ingress |
| `/etc/wireguard/private.key`, `wg0.conf` | Private key/config root-only trên từng VM |
| `/opt/dcs29/postgres.env`, `secret.env` | Password/DSN root-only; không cat ra console hoặc commit |
| Secret `postgres-auth`, `issues-db` | Credential PostgreSQL và DSN app trong mỗi cluster |
| PVC `postgres-data` AWS | Dữ liệu primary trên OS disk |
| PVC `postgres-data-standby-initial` Azure | Dữ liệu standby trên OS disk riêng |
| `/opt/dcs29/automation-check.json` | Request ID/body smoke issue lưu **trước** POST |
| `/opt/dcs29/automation-result.json` | Kết quả xác minh, không chứa credential |

Password PostgreSQL/WireGuard private key không đi qua Terraform state hoặc cloud-init/user_data. Password AWS được chuyển qua SSH, giữ trong bộ nhớ của Python laptop rồi gửi tới Azure; script không ghi chúng thành file trên laptop. Đường dẫn SSH private key và metadata public có thể nằm trong state, nội dung private key không nằm trong state.

Kubernetes Secrets không được coi là mã hóa chỉ vì lưu base64. Kubeconfig và các file root phải được bảo vệ; bản sao lưu credential cần được giữ ngoài VM ở nơi riêng. Automation không tạo secret vault/KMS hoặc backup ngoài VM.

Schema hiện có gồm `app_control`, `issues`, `comments`, `issue_history`, `save_requests`; lấy từ code `database/`, không hardcode một schema khác trong Terraform. Chỉ primary chạy initialize; Azure nhận toàn bộ dữ liệu/schema/role qua physical replication. `issues_app` sở hữu database và có `pg_read_all_stats` để API báo được trạng thái WAL receiver; không dùng superuser cho app.

PVC yêu cầu 5 GiB là mức lập kế hoạch, **local-path không giới hạn cứng dung lượng**. WAL, image/container logs và bản clone/rejoin bổ sung vẫn dùng cùng OS disk 30 GiB. PV được patch Retain sau khi database sẵn sàng; policy này không bảo vệ dữ liệu khi xóa VM/OS disk. Theo dõi dung lượng và sao lưu ngoài VM trước destroy.

## 4. Retry và các điểm dừng

Script giữ key/password và dữ liệu đã tồn tại, apply manifest idempotent, sử dụng request ID cũ khi gửi lại smoke issue. Ingress HTTP đã có được giữ nguyên để không ghi đè redirect HTTPS được cấu hình về sau.

Nếu Azure copy ban đầu bị dở, không khởi động từ một PG_VERSION chưa được xác minh. Init container yêu cầu `.clone-complete`; khi không có marker hoặc PGDATA có dữ liệu dở thì dừng. Sau khi đọc log, đổi `standby_storage_generation="retry1"` để tạo PVC mới, giữ PVC cũ. Cơ chế này dành cho initial clone chưa từng có result thành công; standby đã nghiệm thu nhưng offline phải phục hồi theo runbook DR.

Để yêu cầu chạy lại orchestration trên laptop, từ `deploy/terraform`:

```powershell
terraform plan -replace='terraform_data.workloads[0]' -out=retry.tfplan
# Kiểm tra không có replace VM/disk ngoài dự kiến.
terraform apply retry.tfplan
```

Nếu database không thuộc automation, credential file bị mất nhưng Secret còn, password AWS/Azure khác nhau, K3s đang bị guard chặn, AWS đã standby, Azure đã primary hoặc gate bị khóa: script dừng để người vận hành xử lý. Không bỏ check hoặc regenerate password nhằm ép apply chạy tiếp. Không tự import database Compose, khôi phục backup cũ, xóa PVC cũ hoặc nâng major PostgreSQL.

## 5. Reboot, failover và các phần vẫn làm tay

Guard `/etc/systemd/system/k3s.service.d/10-dr-start-guard.conf` yêu cầu `/run/issues-k3s-start-approved`. Marker chỉ tồn tại trong boot đang chạy, mất sau reboot; tránh AWS primary cũ tự chạy lại khi Azure đã promote. Guard không thay thế fencing cloud.

Sau restart bình thường, chỉ khi đã xác nhận chưa có failover và đúng role trước khi tắt, người vận hành mới touch marker/start K3s, rồi kiểm tra lại streaming/writer. Sau failover phải rejoin DB cũ bằng PVC mới trước failback. Hướng dẫn chi tiết nằm ngay ở mục 7.3 và 9.1 của [hướng dẫn triển khai](TRIEN_KHAI_BANG_TERRAFORM.md).

Các bước vẫn do người vận hành thực hiện:

- Chọn region/quota/image, đăng nhập cloud, SSH key, tfvars và xem plan trước apply.
- Delegate domain, chuẩn bị credential DNS Azure và bật HTTPS automation; certificate/TLS Secret, HTTPS Ingress và renewal tự chạy.
- Sau HTTPS/writer check, bật Terraform health check rồi DNS failover theo hai lượt.
- Diễn tập GĐ6A thủ công trước khi bật GĐ6B. Khi `enable_auto_failover=true`, watcher trên VM Azure tự fence AWS/promote Azure ([chi tiết](AUTOMATION_FAILOVER.md)); planned drain, rejoin, failback và rearm vẫn do người vận hành thực hiện. Không chạy promote tay song song với watcher.
- Backup ngoài VM, monitoring Prometheus/Grafana độc lập, thu thập metrics và đo RTO/RPO.

DNS chỉ chọn origin; không promote PostgreSQL hoặc đồng bộ bản nháp chưa gửi. Async replication có thể mất giao dịch đã commit nhưng chưa replay khi primary sập. Request chưa nhận ACK cần tra receipt/request ID trước khi gửi lại.

## 6. Kiểm thử đã thực hiện

- Terraform fmt/validate và 11 mock-provider tests: hạ tầng, chế độ infra-only, thiếu đầu vào, health check/DNS guard; không tạo tài nguyên cloud.
- Python offline tests: thứ tự schema trước standby, Azure promote chặn trước mutation, clone marker/PVC generation, request ID được lưu trước POST và retry, validation key/image.
- Container cách ly dùng manifest/config thực tế: SQL role/schema, `database.issues.initialize()`, base backup, tạo lại passfile sau restart init, API primary/standby/streaming với user app, replay issue/history và POST idempotent.

Chưa xác minh SSH/cloud-init, WireGuard handshake, EndpointSlice routing, Traefik và reboot/rejoin trên hai VM thật. Chỉ đánh dấu cloud pass sau khi chạy checklist mục 5–10 của hướng dẫn triển khai và lưu evidence.

[Quay lại nhánh tự động](README.md).
