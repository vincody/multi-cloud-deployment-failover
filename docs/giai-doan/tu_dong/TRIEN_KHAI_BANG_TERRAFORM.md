# Triển khai Issues bằng Terraform

Terraform tạo hai VM/K3s, WireGuard, PostgreSQL primary/standby, schema và app Issues. Người triển khai chuẩn bị đầu vào, chạy từng lượt plan/apply và kiểm tra kết quả; không chạy lại các bước Terraform đã cấu hình.

## Đọc nhanh trước khi triển khai

| Nội dung | Cần làm |
|---|---|
| Chuẩn bị | AWS/Azure account, domain, SSH key, Python/CLI, image và K3s version đã pin |
| Lượt 1: hạ tầng + HTTP | [Điền tfvars](#3-điền-cấu-hình-terraform) → [plan/apply](#4-kiểm-tra-và-tạo-hạ-tầng) → [kiểm tra role/streaming từ laptop](#71-những-gì-đã-được-triển-khai-và-cách-kiểm-chứng) |
| Lượt 2: HTTPS | [Delegate domain, chuẩn bị quyền DNS Azure, bật HTTPS và kiểm tra](#8-tự-động-domain-certificate-ingress-https-và-renewal) |
| Lượt 3: health check | [Bật health check, kiểm tra đúng trạng thái primary/standby](#9-bật-health-check-và-dns-failover-theo-hai-lượt) |
| Lượt 4: DNS failover | Trong mục 9, chỉ publish DNS sau khi sẵn sàng runbook fencing/promote/rejoin |
| Monitoring | [Cài Prometheus/Grafana và pod metrics](#101-cài-monitoring-và-theo-dõi-pods); Terraform chưa làm phần này |
| Nghiệm thu | [Diễn tập thủ công](#91-gđ6a--failover-rejoin-và-failback-thủ-công), [kiểm thử và đo RTO/RPO](#102-kiểm-thử-và-đo-rtorpo) |
| Lượt 5: tùy chọn | [Bật failover tự động GĐ6B](#92-gđ6b--failover-tự-động-aws--azure-watcher), sau diễn tập GĐ6A, về baseline và có monitoring |
| Chuẩn bị và thực hiện demo | [Runbook sau triển khai đến demo](#13-runbook-sau-triển-khai-đến-demo): kiểm tra baseline, tạo dữ liệu, gây lỗi, xác nhận Azure, rejoin/failback và lưu bằng chứng |
| Vận hành | [Update image, bảo vệ state, dọn lab](#12-cập-nhật-image-bảo-vệ-state-và-dọn-lab); migration/retry/reboot ở cuối file |

**Bắt buộc:** [chuẩn bị công cụ/tài khoản](#2-chuẩn-bị-trên-laptop), thay placeholder, đọc plan trước apply và kiểm tra kết quả từng lượt. Giữ laptop/network hoạt động trong automation. Apply tạo tài nguyên có phát sinh phí.

**Cách đọc:** phần chính là luồng triển khai VM mới. Các mục tham khảo, hướng dẫn vận hành hoặc bước tùy chọn được trình bày trực tiếp bên dưới; đọc khi cần. Toàn bộ lệnh/manifest vẫn nằm trong file này. Giữ số mục gốc để các tham chiếu cũ tiếp tục dùng được.

**Điểm dừng:** apply lỗi thì giữ state/VM/PVC và dùng [mục 7.2](#72-nếu-automation-thất-bại-hoặc-cần-chạy-lại); TLS lỗi dùng [mục 8.5](#85-retry-credential-rotation-và-phạm-vi). Không promote khi tạo health check, không chạy promote tay song song với watcher tự động. Khi bảo trì/rejoin, tạo `watcher.pause` theo mục 9.2.

## 1. Hiểu phạm vi tự động hóa

**Tham khảo — Terraform làm gì và phần nào còn cần vận hành**

| Thành phần | Terraform hiện tại | Việc tiếp theo |
|---|---|---|
| AWS | VPC/subnet, route, SG, EC2 `t3.medium`, gp3 mã hóa 30 GiB, Elastic IP | Automation chờ cloud-init/K3s; chỉ xem log khi lỗi |
| Azure | Resource Group, VNet/subnet, NSG, NIC, Public IP, VM `Standard_B2s`, Standard SSD 30 GiB hoặc lớn hơn theo image | Automation chờ cloud-init/K3s; chỉ xem log khi lỗi |
| Kubernetes | Cài phiên bản K3s đã pin, tạo namespace `issues` trên từng VM mới | Không cài lại K3s/namespace; nâng cấp là quy trình riêng |
| Issues | Tạo Secret, apply Deployment/Service với cùng GHCR digest và HTTP Ingress | Kiểm tra truy cập từ laptop, bật HTTPS ở lượt 2 |
| PostgreSQL | PostgreSQL 17, PVC 5 GiB, primary AWS, schema có sẵn, base backup và streaming standby Azure | Sao lưu ngoài VM, vận hành failover/failback |
| Mạng database | WireGuard `10.200.29.1`/`.2`, UDP 51820 chỉ từ IP peer; PostgreSQL không mở Internet | Automation kiểm chứng bằng clone/streaming; không cấu hình lại tunnel |
| DNS và quyền TLS | Hosted zone, A record, IAM; Certbot/TLS/Ingress/renewal khi bật `enable_https` | Delegate domain, chuẩn bị key DNS Azure và bật HTTPS |
| DNS failover | Tạo health check và CNAME khi bật biến tương ứng | Kiểm chứng writer readiness và quy trình chuyển database trước |
| Failover tự động GĐ6B | Watcher trên VM Azure và IAM user quyền hẹp khi `enable_auto_failover=true` | Tạo access key ngoài Terraform; diễn tập GĐ6A trước; rejoin/failback vẫn thủ công |
| Giám sát | Chưa triển khai | Prometheus/Grafana ngoài hai VM; thu thập pod/database metrics |

Hai VM chạy **hai cụm K3s độc lập**. PostgreSQL standby đồng bộ bằng WAL, không phải bằng việc dùng chung PVC.


## 2. Chuẩn bị trên laptop

1. Kiểm tra Terraform, Python 3.10+, AWS CLI, Azure CLI và OpenSSH đã cài. Cấu hình dùng Terraform từ 1.7 trở lên, dưới 2.0.
2. Có AWS account, Azure subscription và domain thuộc quyền quản lý của bạn.
3. Chọn vùng, kiểm tra quota và khả dụng của `t3.medium`/`Standard_B2s`. Lệnh kiểm tra catalog có ngay bên dưới. B2s thuộc Bv1 có thông báo retirement; kiểm tra vòng đời, quota và khả dụng của vùng trước khi triển khai.
4. Chuẩn bị SSH public key và public IPv4 của laptop dạng `/32`; giữ private key ngoài repo.
5. Chọn GHCR package Public, image `linux/amd64` và một digest cho cả hai bên.
6. Chọn một release K3s đã phát hành, pin cùng phiên bản trên hai VM.
7. Chọn digest công khai của `postgres:17-bookworm` (Debian, user postgres UID 999, linux/amd64). Automation kiểm tra major 17/UID trước khi chạm dữ liệu.

Kiểm tra công cụ và lấy digest từ **PowerShell laptop**:

```powershell
python --version
ssh -V
Test-Path "$env:USERPROFILE/.ssh/dcs29_terraform"
docker buildx imagetools inspect postgres:17-bookworm
```

Lấy `Digest: sha256:...` của image/index hỗ trợ `linux/amd64` rồi điền `postgres_image = "docker.io/library/postgres@sha256:DIGEST_THAT"`. Docker chỉ cần ở laptop cho việc inspect; VM dùng containerd của K3s. Không dùng digest mẫu hoặc image Alpine. Nếu key có passphrase, nạp key bằng `ssh-add` vào SSH agent trước apply vì script dùng `BatchMode=yes`; kiểm tra `ssh-add -l`. Giữ private key ngoài repo. Hai VM cần SSH không tương tác và tài khoản có `sudo -n` (Ubuntu cloud mặc định đáp ứng).

Trong **PowerShell laptop**, đăng nhập và xác nhận đúng tài khoản:

```powershell
terraform version
aws --version
az version
aws sts get-caller-identity
az login
az account set --subscription '<SUBSCRIPTION_ID>'
az account show --query '{subscription:id,tenant:tenantId}' -o table
```

Nếu dùng AWS profile, đặt `$env:AWS_PROFILE = 'TEN_PROFILE'` trước các lệnh AWS/Terraform. Thay placeholder bằng thông số thật; không ghi credential vào repo.

### 2.1. Kiểm tra image và VM size

Trước `terraform plan`, có thể kiểm tra **image và VM size trong đúng region/subscription của bạn** từ PowerShell laptop. Thay ba giá trị dưới bằng `aws_region`, `azure_region` và `azure_vm_size` đã chọn trong `terraform.tfvars`; lệnh chỉ đọc catalog, không tạo VM:

```powershell
$AwsRegion = 'ap-southeast-1'
$AzureRegion = 'indiasouthcentral'
$AzureSize = 'Standard_B2s'
aws ec2 describe-images --region $AwsRegion --owners 099720109477 --filters Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-* Name=architecture,Values=x86_64 Name=virtualization-type,Values=hvm --query 'sort_by(Images,&CreationDate)[-1].[ImageId,Name,Architecture,OwnerId,CreationDate]' --output table
az vm image show --location $AzureRegion --urn Canonical:ubuntu-24_04-lts:server:latest --output table
az vm list-skus --location $AzureRegion --resource-type virtualMachines --size $AzureSize --all --output table
```

AWS phải trả một AMI mới nhất với `OwnerId=099720109477`, tên Ubuntu **24.04 Noble amd64** và `Architecture=x86_64`; AMI ID khác nhau theo region và có thể đổi khi Canonical phát hành bản mới. Azure image phải hiện đúng `Canonical:ubuntu-24_04-lts:server:latest` (Ubuntu 24.04 **Gen2 AMD64**). Trong bảng size, tìm **đúng** `Standard_B2s` và đọc cột `Restrictions`: `NotAvailableForSubscription` nghĩa là size đó chưa dùng được với subscription/vùng hiện tại. Nếu lệnh trả rỗng/lỗi, xử lý vùng, quyền, quota hoặc chọn cấu hình có sẵn **trước khi apply**. [Mẫu AMI và owner Canonical](https://developer.hashicorp.com/terraform/tutorials/aws-get-started/aws-create) · [URN Ubuntu Azure](https://ubuntu.com/azure/docs/azure-how-to/instances/find-ubuntu-images/) · [Kiểm tra Azure SKU](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available).

## 3. Điền cấu hình Terraform

Từ thư mục gốc repository, chạy trên **PowerShell laptop**:

```powershell
Set-Location deploy/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars
```

Chỉ copy khi chưa có `terraform.tfvars`; giữ file đã cấu hình nếu đang tiếp tục một lần triển khai.

| Biến | Nội dung cần điền |
|---|---|
| `aws_region`, `azure_region` | Vùng thực tế đã kiểm tra |
| `azure_subscription_id` | UUID subscription đã chọn |
| `domain_name` | Domain thật, không có `https://` hoặc dấu chấm cuối |
| `ssh_public_key` | Toàn bộ dòng trong file `.pub`; không phải private key |
| `ssh_cidr` | Public IPv4 hiện tại của laptop kèm `/32` |
| `ghcr_image` | Đường dẫn package, không có tag hoặc digest |
| `image_digest` | 64 ký tự hex sau `sha256:` của image được chọn |
| `k3s_version` | Release cụ thể dạng `vX.Y.Z+k3sN`; không dùng placeholder |
| `aws_instance_type` | `t3.medium` |
| `azure_vm_size` | `Standard_B2s` |
| `aws_root_volume_size` | 30 GiB ban đầu; không giảm đĩa đang có dữ liệu |
| `azure_os_disk_size` | 30 GiB; tăng nếu source image yêu cầu lớn hơn, không giảm đĩa cũ |
| `aws_cpu_credits` | `standard`; khi hết credit có thể bị giới hạn CPU |
| `enable_workload_automation` | `true` để triển khai đầy đủ; `false` chỉ tạo hạ tầng/K3s ở lần triển khai mới |
| `ssh_private_key_path` | Đường dẫn tuyệt đối tới private key trên laptop, dùng dấu `/`, ví dụ `C:/Users/ASUS/.ssh/dcs29_terraform` |
| `postgres_image` | `docker.io/library/postgres@sha256:DIGEST_THAT`, PostgreSQL 17 Debian/UID 999 |
| `automation_python` | `python` hoặc đường dẫn tuyệt đối tới Python 3.10+ |
| `enable_https`, `certificate_email` | Ban đầu false; bật true với email thật sau delegation DNS |
| `azure_dns_credentials_file` | Đường dẫn file INI riêng ngoài repo, mục 8.2 |
| `standby_storage_generation` | `initial`; chỉ đổi khi cần thử lại một lần clone Azure ban đầu chưa thành công |

Ở lượt đầu giữ:

```hcl
enable_workload_automation = true
enable_https               = false
enable_health_checks       = false
enable_failover            = false
enable_auto_failover       = false
```

Chưa có TLS/database nên chưa bật kiểm tra HTTPS hoặc publish domain failover. Giữ state an toàn; không commit `.tfvars`, state hoặc plan.

## 4. Kiểm tra và tạo hạ tầng

Vẫn ở `deploy/terraform`, chạy từng lệnh trên **PowerShell laptop**:

```powershell
terraform init
terraform fmt -check
terraform validate
terraform test
terraform plan -out=first.tfplan
```

`terraform test` dùng provider giả, không tạo tài nguyên cloud. Đọc plan: đúng account/subscription, vùng, VM size, disk, SSH CIDR, domain và image. Nếu đã có hạ tầng cùng tên từ Console/Portal, xử lý import hoặc chọn cách quản lý trước khi apply; không xóa state để thử lại.

Sau khi xem plan và quyết định triển khai:

```powershell
terraform apply first.tfplan
terraform output
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
terraform output -json route53_nameservers
```

Apply sẽ tạo tài nguyên có phát sinh phí. Sau khi VM/cloud-init sẵn sàng, `terraform_data.workloads[0]` chạy SSH từ laptop theo thứ tự AWS rồi Azure. Giữ laptop/network hoạt động đến khi có dòng `Automation verified`. Nếu lỗi giữa chừng, giữ nguyên state và làm mục 7.2; không xóa VM/PVC để thử lại.

## 5. Kiểm tra bootstrap trên hai VM

Khi apply có dòng `Automation verified`, script đã chờ cloud-init/K3s, triển khai database/app, kiểm tra role/streaming và issue mẫu trên Azure. Không cần SSH chạy lại các lệnh chờ bootstrap, tạo namespace hoặc apply manifest.

Chuyển tới mục 7.1 để kiểm tra truy cập từ laptop, rồi chuẩn bị domain/HTTPS ở mục 8. Nếu apply lỗi, dùng mục 7.2; không cài lại K3s hoặc chạy lại user_data để thử.

## 6. Mạng bảo vệ và manifest PostgreSQL

**Terraform đã cấu hình — kiến trúc mạng và manifest**

Phần này **Terraform đã làm** khi `enable_workload_automation = true`; không chạy lại các lệnh tự tạo WireGuard, password, PVC hoặc `pg_basebackup` của nhánh thủ công.

- SG AWS và NSG Azure mở UDP 51820 chỉ từ public IP `/32` của VM đối diện.
- Mỗi VM tự sinh và giữ private key tại `/etc/wireguard/private.key`; script chỉ trao đổi public key.
- `wg0`: AWS `10.200.29.1`, Azure `10.200.29.2`; PostgreSQL bind loopback + IP tunnel, không bind public IP.
- Service `postgres` không có selector; EndpointSlice trỏ IP WireGuard của chính VM. App dùng DNS Service nội bộ của cluster; Azure base backup đọc primary qua tunnel.
- Bộ manifest nằm trong `deploy/terraform/automation/templates`, được render và ghi tại `/opt/dcs29` trên VM. PostgreSQL sử dụng local-path PVC trên OS disk, không cần disk riêng cho lab.

Không cần chạy lại lệnh tạo key, tunnel, firewall, Service/EndpointSlice hay manifest database. Automation xác minh kết nối qua base backup và streaming replication; khi có lỗi mới đọc events/log ở mục 7.2. Không mở 5432, 6443 hoặc metrics ra Internet để sửa kết nối.


## 7. PostgreSQL, schema, standby và ứng dụng

### 7.1. Những gì đã được triển khai và cách kiểm chứng

AWS tự tạo database `issues`, role `issues_app`/`replicator`, Secret `postgres-auth`/`issues-db`, StatefulSet/PVC; chạy `database.issues.initialize()` từ image app **chỉ trên primary**. Schema gồm `app_control`, `issues`, `comments`, `issue_history`, `save_requests`.

Azure nhận credential qua SSH, tạo PVC `postgres-data-standby-initial`, thực hiện `pg_basebackup -R -X stream` và streaming replication. Không tạo schema độc lập trên Azure. File passfile được tạo lại mỗi lần pod khởi động; clone chưa hoàn tất sẽ bị chặn bằng marker `.clone-complete` để tránh vô tình khởi động thành primary.

Các password được sinh trên AWS, giữ trong file root-only `/opt/dcs29/postgres.env` và Kubernetes Secrets của từng cluster. Private key WireGuard và password không được tạo bởi Terraform hoặc đưa vào Terraform state/user_data. Không in các file credential/Secret ra console để debug. Kubernetes Secrets cần quyền quản trị chặt chẽ và bản sao lưu an toàn; base64 không phải mã hóa.

Automation tạo một issue có tên **Replication smoke check** trên AWS, dùng request ID lưu trước POST; đọc lại issue/history/comments từ Azure, kiểm tra role/streaming và HTTP Ingress. Chạy lại không nhân đôi issue này.

**Kiểm tra từ laptop sau lượt 1** để xác nhận public IP/firewall hoạt động; đây không phải bước cấu hình lại database. Automation đã kiểm tra cùng commit, schema, streaming và issue mẫu từ trong VM.

```powershell
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
Invoke-RestMethod "http://$AwsIp/api/database/status"
Invoke-RestMethod "http://$AzureIp/api/database/status"
```

AWS phải là `primary`, `writable=true`; Azure `standby`, `writable=false`, `replication_connected=true`. Mở `http://AWS_IP` để xem ứng dụng và `http://AZURE_IP` để đọc dữ liệu đã replicate. Sau lượt HTTPS, dùng hostname origin thay HTTP IP vì Ingress đã cấu hình theo domain.

Không chạy lại bootstrap schema, tạo role/Secret, base backup, apply app hoặc tự tạo issue mẫu bằng SQL. Kết quả automation được lưu trong `/opt/dcs29/automation-result.json` trên hai VM; chỉ cần đọc file/log này khi debug hoặc thu thập evidence.

> **Trong mọi lượt apply tiếp theo:** Giữ `enable_workload_automation=true` sau khi đã triển khai: đổi thành false sẽ gỡ rule WireGuard và resource orchestration khỏi state, có thể làm mất replication. Bật health check/DNS không làm script chạy lại nếu VM, image, cấu hình automation và bộ script không đổi.

## 8. Tự động domain certificate, Ingress HTTPS và renewal

HTTPS là lượt apply riêng sau khi domain đã delegate. Terraform tạo `terraform_data.https`, dùng SSH để cài Certbot/DNS-01, cấp certificate, tạo TLS Secret `issues-tls`, cập nhật Ingress và lịch gia hạn. User **không cần SSH cấu hình Certbot hoặc tự dán YAML TLS**.

### 8.1. Đổi nameserver tại nhà đăng ký domain

Sau lượt apply HTTP, từ **PowerShell laptop**, thư mục `deploy/terraform`:

```powershell
terraform output -json route53_nameservers
$Domain = 'YOUR_DOMAIN'
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
```

Thay YOUR_DOMAIN bằng `domain_name` thật. Trong trang domain của **nhà đăng ký**, chọn Nameservers → Custom và thay bằng đủ bốn nameserver Terraform xuất ra. Không tạo hosted zone khác hoặc tạo lại hai A record origin. Nếu domain có website/email, sao chép các record cần giữ vào zone mới trước đổi NS; xử lý DS/DNSSEC cũ tại nhà đăng ký nếu có.

Sau khi đổi nameserver, chờ DNS cập nhật rồi chạy lượt HTTPS. Automation tự kiểm tra NS/A qua hai resolver công khai và dừng trước cấp certificate nếu delegation hoặc IP chưa đúng; không cần chạy lại lệnh kiểm tra DNS trước mỗi apply. Nếu chưa cập nhật, chờ thêm rồi retry ở mục 8.5. Nếu domain có CAA hạn chế nhà cấp, cho phép Let's Encrypt. Record `app` chưa cần tồn tại vì cấp bằng DNS-01; TLS certificate vẫn chứa hostname chung.

### 8.2. Chuẩn bị quyền DNS cho Azure, không ghi credential vào state

AWS lấy credential tạm từ EC2 IAM role Terraform đã gắn. Azure dùng IAM user DNS-only Terraform đã tạo; không tạo access key bằng resource Terraform vì secret sẽ vào state.

Trong **AWS Console → IAM → Users → dcs29-azure-certbot → Security credentials → Access keys → Create access key**, chọn Application running outside AWS, tạo và lưu key vào nơi quản lý secret riêng. Không dùng key admin/laptop. User này chỉ được sửa các TXT `_acme-challenge` đã giới hạn bởi policy Terraform.

Trên **PowerShell laptop**, tạo file riêng **ngoài repository**; nếu file có sẵn chỉ mở chỉnh sửa, không ghi đè:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE/.ssh"
notepad "$env:USERPROFILE/.ssh/dcs29-azure-dns.ini"
```

Dán nội dung sau và thay hai placeholder bằng key thật; lưu file ở đúng tên, không thêm `.txt`:

```ini
[default]
aws_access_key_id = ACCESS_KEY_ID_THAT
aws_secret_access_key = SECRET_ACCESS_KEY_THAT
```

Giữ file chỉ cho tài khoản của bạn/administrator đọc; không commit, chụp màn hình hoặc cat nội dung. Không dùng session token hết hạn cho renewal lâu dài. Terraform chỉ nhận **đường dẫn**; Python đọc nội dung trên laptop, gửi qua SSH stdin và ghi Azure `/root/.aws/credentials` quyền 0600. EC2 không nhận file key này.

Nếu Azure đã có credential root được cấu hình trước đó, có thể để `azure_dns_credentials_file=""` để dùng lại; vẫn phải đảm bảo đúng profile `[default]` và quyền DNS. Đổi nội dung file cùng đường dẫn không tạo trigger Terraform; khi rotate key, yêu cầu chạy lại HTTPS resource ở mục 8.5.

### 8.3. Bật HTTPS và apply

Sửa `terraform.tfvars` trên laptop:

```hcl
enable_https = true
certificate_email = "EMAIL_THAT_CUA_BAN"
azure_dns_credentials_file = "C:/Users/YOUR_USER/.ssh/dcs29-azure-dns.ini"
enable_health_checks = false
enable_failover = false
```

Thay email/đường dẫn thật, giữ `enable_workload_automation=true`. Script không hỏi xác nhận tương tác; bật HTTPS đồng nghĩa cho phép đăng ký Let's Encrypt với email đã điền và chấp nhận điều khoản ACME.

```powershell
terraform plan -out=https.tfplan
# Đọc plan: dự kiến thêm terraform_data.https, không replace VM/disk.
terraform apply https.tfplan
terraform output https_automation
```

Script kiểm tra app/K3s trên cả hai VM, cài công cụ, kiểm tra DNS rồi tạm dừng hai renewal timer. Cấp certificate **AWS trước, Azure sau** vì cả hai certificate chứa `app.DOMAIN`. Certificate AWS gồm `app` + `aws-origin`; Azure gồm `app` + `azure-origin`. Retry giữ certificate còn hạn và không force-renew không cần thiết.

Sau cấp, script tạo TLS Secret, HTTPS Ingress `issues-https`, Middleware `https-redirect` và cập nhật `issues-http` thành redirect theo đúng hostname. Không tạo NGINX/Compose. Nếu UFW đang active, tự mở TCP 443; SG/NSG đã mở 443. Truy cập HTTP bằng IP có thể trả 404 sau HTTPS vì ingress đã dùng hostname; dùng URL origin/domain để kiểm tra.

### 8.4. Kiểm chứng HTTPS và gia hạn

Trên **PowerShell laptop**:

```powershell
curl.exe --fail-with-body "https://aws-origin.$Domain/version"
curl.exe --fail-with-body "https://azure-origin.$Domain/version"
curl.exe --fail-with-body --resolve "app.${Domain}:443:${AwsIp}" "https://app.$Domain/version"
curl.exe --fail-with-body --resolve "app.${Domain}:443:${AzureIp}" "https://app.$Domain/version"
curl.exe -I "http://aws-origin.$Domain/version"
curl.exe -I "http://azure-origin.$Domain/version"
```

Hai origin đúng environment/cùng commit. Hostname chung ép đến từng IP phải qua kiểm tra TLS thật; không `-k`. HTTP phải redirect HTTPS. Chỉ khi đã publish failover DNS ở mục 9 mới kiểm tra `app` bằng DNS thật.

Automation cũng kiểm tra `/version` bằng HTTPS trên từng VM với SNI/hostname, public trust chain và redirect. Kết quả ghi `/opt/dcs29/https-result.json`. Nó không thay kiểm tra truy cập Internet từ laptop.

Renewal tự chạy qua `certbot.timer`: AWS 00:00/12:00 UTC, Azure 06:00/18:00 UTC, không random delay hoặc catch-up lúc boot. Service giới hạn 45 phút để giữ các lượt bình thường cách xa nhau; deploy hook cập nhật `issues-tls` khi gia hạn thành công. Lịch lệch giờ giảm xung đột TXT; **không phải distributed lock**. Không chạy renewal/cấp lại thủ công đồng thời hai VM, kiểm tra đồng bộ giờ hệ thống và theo dõi lỗi renewal.

Không cần chạy lại lệnh cấp certificate, tạo TLS Secret/Ingress, bật timer hay gia hạn thủ công. Automation đã cấu hình các phần đó; chỉ kiểm tra truy cập từ laptop bằng block phía trên. Thử ACME renewal bằng dry-run là kiểm thử tùy chọn riêng, không phải bước triển khai bắt buộc.

VM tắt thì không gia hạn. Khi bật sau thời gian dài, xác nhận writer/guard trước khi start K3s, kiểm tra hạn certificate rồi chạy lại HTTPS automation nếu cần.

## 9. Bật health check và DNS failover theo hai lượt

Sau khi TLS, database và replication được kiểm chứng, sửa:

```hcl
enable_health_checks = true
enable_failover      = false
```

Thời gian health check và DNS quyết định phần lớn failover time trên cloud, nên chỉnh được bằng biến (giá trị dưới là mặc định): `health_check_interval_seconds = 30` (chỉ nhận 10 hoặc 30; 10 là fast interval, tính thêm phí mỗi health check), `health_check_failure_threshold = 3`, `dns_ttl_seconds = 30` cho cặp CNAME `app`. Thời gian Route 53 cần để đánh dấu origin lỗi là `interval × threshold` (mặc định 90 giây); `terraform output dns_failover_timing` cho giá trị đang áp dụng. Giữ nguyên các giá trị này giữa các lượt đo (mục 13) và ghi vào manifest.

Trên **PowerShell laptop**, ở `deploy/terraform`:

```powershell
terraform plan -out=checks.tfplan
terraform apply checks.tfplan
terraform output health_check_ids
```

Kiểm tra endpoint HTTPS origin, port 443, SNI và path `/health/database`. Trong trạng thái bình thường **AWS primary Healthy, Azure standby Unhealthy**; không đợi cả hai Healthy cùng lúc.

Sau khi đã chuẩn bị và kiểm chứng quy trình fencing/promote/rejoin, bật:

```hcl
enable_health_checks = true
enable_failover      = true
```

```powershell
terraform plan -out=failover.tfplan
terraform apply failover.tfplan
```

Plan phải thêm đúng cặp CNAME failover. Không tạo lại health check/record do Terraform quản lý bằng Console.

### 9.1. GĐ6A — Failover, rejoin và failback thủ công

**Chỉ khi diễn tập hoặc có sự cố — runbook thủ công**

Các lệnh dưới thuộc **vận hành khi có sự cố hoặc diễn tập**, không phải bước cấu hình sau apply. Khi `enable_auto_failover=false`, fencing/promote thực hiện thủ công. GĐ6B ở mục 9.2 (watcher trên Azure) có thể tự fence AWS và promote Azure; rejoin/failback vẫn thủ công. Không chạy lệnh promote tay song song với watcher đang hoạt động.

Chỉ thực hiện các lệnh chuyển role sau khi baseline và DNS phần 9 đã được cấu hình. Không promote trong lúc tạo health checks.

#### 4. Chuyển role database — trước khi thử DNS failover

> **Tương ứng với script local:** 4.1 + 4.2 + 4.3 là `switchover` (AWS còn sống, không mất dữ liệu); khi AWS đã sập thật bỏ 4.1 và làm 4.2 + 4.3, tương ứng `failover` (có thể mất giao dịch chưa kịp replicate). Watcher của nhánh Terraform tự thực hiện đúng chuỗi `failover` này.

**Thứ tự thao tác:** đọc mục này trước để hiểu runbook, nhưng tạo record bằng Terraform ở phần 9 và xác nhận baseline AWS qua các lệnh quan sát bên dưới trước khi thực hiện chuyển role. Các lệnh mục 4 chỉ chạy trong lượt thử chuyển đổi đã chuẩn bị; không promote ngay khi đang tạo health checks.

##### 4.1. AWS còn truy cập được: khóa ghi và lấy WAL cuối

Chỉ trên **primary AWS đã xác minh**, thay DB_ADMIN_USER và APP_DATABASE. Không chạy lệnh này trên standby. Advisory lock dùng cùng khóa (2900,1) với writer của ứng dụng:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=false WHERE singleton=true; COMMIT;"
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_current_wal_flush_lsn();'
```

Đợi lệnh đầu success rồi mới lấy LSN. Nếu timeout/error, dừng xử lý, không tiếp tục promote. Khóa chờ các transaction app kết thúc; sau commit writer mới thấy writes_enabled=false. Writer DB trực tiếp ngoài ứng dụng cũng phải dừng riêng.

Trên Azure, so replay với LSN vừa ghi, thay FINAL_WAL_LSN:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(), pg_last_wal_replay_lsn() >= 'FINAL_WAL_LSN'::pg_lsn AS caught_up;"
```

Chỉ tiếp khi recovery=true, caught_up=true. Đối chiếu dữ liệu/receipt mới nhất. Nếu lag chưa bắt kịp, chờ/kiểm tra streaming, không đổi đích DNS.

##### 4.2. Fence AWS

AWS Console → EC2 → Instances → chọn đúng AWS_VM_ID → Instance state → Stop instance. Đợi trạng thái **Stopped**, xác minh IP đúng instance và không có cơ chế tự bật writer khác. Stop command được gửi chưa đủ để chứng minh fencing.

K3s StatefulSet sẽ tạo lại pod bị xóa, nên kubectl delete pod postgres-0 không phải fencing. Nếu dùng phương án cô lập thay Stop, phải chứng minh writer không nhận được ghi và không tự tái xuất hiện. Thiếu xác nhận thì dừng promote.

AWS sập bất ngờ: bỏ qua bước drain nếu không thể truy cập, nhưng vẫn xác minh fencing, WAL Azure và ghi rõ nguy cơ mất giao dịch async. Không cam kết RPO=0.

##### 4.3. Promote và mở ghi Azure

Chỉ sau xác nhận AWS đã bị fence, trên SSH Azure:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_promote(true,30);'
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_is_in_recovery();'
```

Promote phải success, recovery=false; nếu chưa đúng không mở ghi. Sau đó mở gate của primary mới:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=true WHERE singleton=true; COMMIT;"
```

Laptop thử /health/database Azure: HTTP 200/writable=true; thử tạo bình luận qua origin và kiểm tra request_id không trùng. Chỉ sau đó cho shared traffic sang Azure. Writer check mới healthy có độ trễ; client DNS có cache riêng.

#### 6. LAPTOP — quan sát tuyến shared và ghi dữ liệu

Đặt SHARED_HOST thực tế; gọi qua DNS thật, không --resolve:

```powershell
Resolve-DnsName app.YOUR_DOMAIN -Type A -Server 1.1.1.1
curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/issues
```

Ban đầu AWS phục vụ; sau mục 4 Azure mới đủ điều kiện phục vụ ghi. Ghi UTC DNS đổi, giao dịch ghi thành công đầu tiên và lỗi client. 30 giây × threshold không phải RTO chắc chắn.

Khi cả hai writer check unhealthy, Route 53 có thể vẫn trả record theo hành vi fail-open; backend phải tiếp tục chặn ghi sai role. DNS không thay khóa writer. [AWS lựa chọn record](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-how-route-53-chooses-records.html).

#### 7. Dữ liệu sau failover

Giữ cùng URL HTTPS /issues.html, tạo/sửa/bình luận/đóng issue trên Azure. Ghi ID/version/request_id, nội dung và lịch sử. Retry không trùng; bản nháp chưa gửi vẫn ở cùng localStorage và không phải giao dịch DB đã commit.

Giữ AWS cũ bị fence. Không Start EC2 với PostgreSQL primary cũ tự khởi động; trước khi bật lại phải có cơ chế chặn workload DB cũ và endpoint writer. Việc này cần runbook/manifest cloud phù hợp, chưa có script tự động trong repo.

#### 8. Rejoin AWS và failback có kiểm soát

1. Azure tiếp tục nhận ghi. AWS phục hồi trong trạng thái bị chặn writer cũ; ghi inventory PV/path và giữ storage cũ.
2. Tạo storage mới, clone AWS standby từ Azure bằng quy trình GĐ4 theo chiều ngược. Không init AWS thành primary mới độc lập.
3. Đợi streaming/replay và đối chiếu toàn bộ dữ liệu mới trên Azure.
4. Khóa ghi Azure/drain bằng câu lệnh mục 4.1 trên primary Azure; lấy WAL cuối, so với replay AWS.
5. Fence Azure bằng Portal/cơ chế đã kiểm chứng; promote AWS và mở gate theo mục 4.3 trên AWS.
6. Kiểm tra /health/database AWS 200 và dữ liệu, rồi Route 53 mới đủ điều kiện quay về AWS. VM AWS Running hoặc /health/live 200 chưa đủ điều kiện failback.
7. Dựng Azure standby từ AWS trên storage mới, xác nhận streaming và cả hai API.
8. Sau verify mới dọn đúng DB/PV cũ không dùng; giữ bản gần nhất ngắn hạn nếu cần đối chiếu.


##### 8.1. Khôi phục VM cũ mà không bật primary cũ

Điều kiện: đã cấu hình startup guard `/run/issues-k3s-start-approved` ở hướng dẫn AWS/Azure **trước** khi gây lỗi. Sau failover, Start VM cũ và SSH; K3s phải bị chặn vì file /run chưa tồn tại. Không touch file phê duyệt trước các bước chuyển storage dưới.

```bash
sudo systemctl stop k3s
test ! -e /run/issues-k3s-start-approved
sudo systemctl status k3s --no-pager
```

Lệnh status có thể trả mã khác 0 khi service failed/inactive; đây là trạng thái mong đợi lúc đang rejoin. Kiểm tra `ps`/`sudo ss -ltnp` không còn PostgreSQL lắng nghe tunnel 5432. Nếu còn tiến trình K3s/containerd cũ sau reboot bất thường, dừng xử lý và xác minh chúng đã dừng trước khi cho VM kết nối lại mạng DB. Startup guard chặn K3s, không thay fencing cloud.

##### 8.2. Tạo một PVC mới, giữ nguyên storage cũ

K3s bị chặn nên chưa chạy kubectl. Trên VM cũ cài parser để sửa manifest local. Dùng shell root vì file automation thuộc root; giữ phiên root cho các bước sửa file/rejoin bên dưới và `exit` khi hoàn tất:

```bash
sudo apt-get install -y python3-yaml
sudo -i
cd /opt/dcs29
cp -n postgres.yaml postgres-before-rejoin.yaml
nano rejoin-init.yaml
```

Dán init container sau vào rejoin-init.yaml. Mẫu này **AWS rejoin từ Azure**, SOURCE_DB_HOST=`10.200.29.2`. Khi Azure rejoin từ AWS ở cuối vòng, đổi host thành `10.200.29.1`:

```yaml
name: base-backup
image: POSTGRES_IMAGE_DIGEST
envFrom:
  - secretRef: {name: postgres-auth}
env:
  - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
  - {name: SOURCE_DB_HOST, value: "10.200.29.2"}
command: [bash, -ec]
args:
  - |
    install -d -o postgres -g postgres -m 700 /var/lib/postgresql/repl
    printf '%s:5432:*:replicator:%s\n' "$SOURCE_DB_HOST" "$REPLICATION_PASSWORD" > /var/lib/postgresql/repl/pgpass
    chown postgres:postgres /var/lib/postgresql/repl/pgpass
    chmod 600 /var/lib/postgresql/repl/pgpass
    export PGPASSFILE=/var/lib/postgresql/repl/pgpass
    if [ -s "$PGDATA/PG_VERSION" ]; then
      test -f /var/lib/postgresql/data/.clone-complete || { echo "Incomplete clone: refuse startup"; exit 1; }
      exit 0
    fi
    mkdir -p "$PGDATA"
    test -z "$(ls -A "$PGDATA")" || { echo 'Partial copy: choose a new PVC'; exit 1; }
    chown postgres:postgres "$PGDATA"
    gosu postgres pg_basebackup -d "host=$SOURCE_DB_HOST port=5432 user=replicator passfile=$PGPASSFILE" -D "$PGDATA" -R -X stream -c fast
    test -f "$PGDATA/standby.signal"
    touch /var/lib/postgresql/data/.clone-complete
volumeMounts:
  - {name: data, mountPath: /var/lib/postgresql/data}
  - {name: repl-pass, mountPath: /var/lib/postgresql/repl}
```

Thay digest đúng bản PostgreSQL đang chạy. Clone dùng password replication vẫn có trong Secret cũ; nếu đã rotate phải cập nhật Secret trước. ConfigMap listen_addresses phải là tunnel IP **VM đang rejoin**, không phải IP source.

Tạo manifest `postgres-rejoin.yaml` với PVC mới và replicas=0; code chỉ viết file, không xóa tài nguyên:

```bash
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import yaml
docs=list(yaml.safe_load_all(Path('postgres.yaml').read_text()))
clone=yaml.safe_load(Path('rejoin-init.yaml').read_text())
new_name='postgres-data-rejoin-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
for doc in docs:
    if doc['kind']=='PersistentVolumeClaim':
        doc['metadata']['name']=new_name
    if doc['kind']=='StatefulSet':
        doc['spec']['replicas']=0
        pod=doc['spec']['template']['spec']
        pod['initContainers']=[clone]
        for volume in pod['volumes']:
            if volume['name']=='data': volume['persistentVolumeClaim']['claimName']=new_name
Path('postgres-rejoin.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
print('New PVC:',new_name)
PY
```

Kiểm tra file và ghi PVC mới vào inventory. Không tiếp tục nếu không đủ dung lượng giữ cả hai bản DB trên OS disk 30 GiB; tăng đĩa hoặc lưu backup ngoài VM trước.

##### 8.3. Cài manifest vào hàng đợi trước khi K3s khởi động

Để kubelet không khởi động pod primary cũ trong lúc sửa StatefulSet, K3s khởi động tạm **không có agent**, rồi scale/update workload trước khi cho kubelet chạy. Trên VM rejoin:

```bash
sudo install -d -m 700 /etc/rancher/k3s/config.yaml.d
sudo tee /etc/rancher/k3s/config.yaml.d/99-rejoin.yaml >/dev/null <<'EOF'
disable-agent: true
EOF
sudo touch /run/issues-k3s-start-approved
sudo systemctl reset-failed k3s
sudo systemctl start k3s
sudo k3s kubectl -n issues scale deployment/issues-app --replicas=0
sudo k3s kubectl -n issues scale statefulset/postgres --replicas=0
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres-rejoin.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres-rejoin.yaml
sudo k3s kubectl -n issues delete pod postgres-0 --ignore-not-found --force --grace-period=0
sudo k3s kubectl -n issues get pods -l app=postgres
```

Chế độ disable-agent là tính năng K3s; kiểm tra release pin hỗ trợ trước demo. Không dùng k3s-killall/uninstall vì có thể thay đổi runtime/mạng/storage ngoài ý muốn. Trong server không agent, node cũ có thể NotReady; controller/API vẫn dùng được. Không xác minh được DB cũ đã dừng thì giữ VM bị fence.

Vì kubelet đang tắt và PostgreSQL cũ đã xác nhận dừng, xóa Pod object cũ để nó không được kubelet phục hồi từ manifest cũ. Chỉ khởi động agent khi không còn Pod postgres cũ và StatefulSet đã trỏ PVC mới với replicas=0:

```bash
sudo k3s kubectl -n issues get statefulset postgres -o jsonpath='{.spec.template.spec.volumes}'
sudo k3s kubectl -n issues get pvc
sudo mv /etc/rancher/k3s/config.yaml.d/99-rejoin.yaml /opt/dcs29/99-rejoin.yaml.disabled
sudo systemctl restart k3s
sudo k3s kubectl -n issues scale statefulset/postgres --replicas=1
sudo k3s kubectl -n issues rollout status statefulset/postgres --timeout=300s
sudo k3s kubectl -n issues logs postgres-0 -c base-backup --tail=100
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -c 'SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(); SELECT status FROM pg_stat_wal_receiver;'
```

Rejoin phải recovery=true/streaming. Cập nhật EndpointSlice postgres-tunnel vẫn về tunnel IP local; Service app không đổi DSN. Đặt PV mới Retain rồi bật app trong phiên SSH root:

```bash
NEW_PVC=$(sudo k3s kubectl -n issues get statefulset postgres -o jsonpath='{.spec.template.spec.volumes[?(@.name=="data")].persistentVolumeClaim.claimName}')
NEW_PV=$(sudo k3s kubectl -n issues get pvc "$NEW_PVC" -o jsonpath='{.spec.volumeName}')
test -n "$NEW_PV" && sudo k3s kubectl patch pv "$NEW_PV" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
sudo k3s kubectl -n issues scale deployment/issues-app --replicas=1
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=180s
```

Sau kiểm chứng, lưu manifest active (replicas=1) trong phiên root:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml
docs=list(yaml.safe_load_all(Path('/opt/dcs29/postgres-rejoin.yaml').read_text()))
for doc in docs:
    if doc['kind']=='StatefulSet': doc['spec']['replicas']=1
Path('/opt/dcs29/postgres.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
PY
```

Không chạy lại Terraform initial automation trong trạng thái role/storage đã rejoin; không apply lại postgres-before-rejoin.yaml vì nó trỏ dữ liệu cũ. Đối chiếu issue/comment mới Azure trên AWS trước failback. Init container tái tạo passfile mỗi lần pod được tạo, kể cả khi PGDATA đã tồn tại.

##### 8.4. Failback

Sau AWS standby đã bắt kịp, chạy drain/gate/WAL mục 4.1 **trên Azure primary**, so replay AWS. Stop Azure VM và đợi Stopped/Deallocated đúng máy. Promote AWS, mở gate theo mục 4.3; kiểm tra shared URL ghi được và Route 53 chọn AWS. Cuối cùng rejoin Azure bằng các bước 8.1–8.3 với source AWS `10.200.29.1`, PVC mới. Không init schema lần nữa.

Nguồn: [K3s server flags, disable-agent](https://docs.k3s.io/cli/server), [pg_basebackup](https://www.postgresql.org/docs/17/app-pgbasebackup.html). Đây là runbook demo cần thử trên VM trước buổi đo; không phải hệ thống tự động recovery đã nghiệm thu.


#### 9. Lỗi và điểm dừng

| Hiện tượng | Hành động |
|---|---|
| Standby writer check unhealthy | Bình thường khi writable=false; kiểm tra streaming/API đọc |
| Gate lock timeout | Không promote; kiểm tra transaction đang chạy và writer trực tiếp |
| Không xác minh được fencing | Dừng chuyển primary, tránh hai writer |
| Promote/verify lỗi | Chưa đổi DNS/gate; ghi phase/log để xử lý |
| DNS còn IP cũ | So authoritative/recursive, TTL/cache, backend cũ vẫn phải bị fence |
| AWS tự quay lại | Kiểm tra writer eligibility, không dùng /health/live cho failback |

#### 10. Nghỉ, rollback và evidence

Ghi writer thực tế và phase trước khi stop/deallocate. Hosted zone/health checks/disk/IP có thể tiếp tục tính phí. Rollback DNS không phải rollback dữ liệu: sau khi Azure đã nhận ghi, không trả tuyến về AWS DB cũ.

Giữ cấu hình Health check/record, UTC từng bước, WAL cutoff/replay, fencing trạng thái VM, promote/gate, dữ liệu/receipt và raw metrics. Che credential, không sửa raw data.

#### 11. Checklist nghiệm thu

- [ ] Health check/record đúng origin, path writer readiness và SNI.
- [ ] AWS primary/Azure standby được phân biệt, không yêu cầu cả hai writer check healthy.
- [ ] Fence được xác minh trước promote; chỉ một writer.
- [ ] Dữ liệu Azure mới về AWS trước failback; không chỉ đổi DNS.
- [ ] URL/TLS/bản nháp và retry ổn định; Azure trở lại standby sau vòng.
- [ ] Có recovery runbook cho thao tác dở và inventory DB cũ.


### 9.2. GĐ6B — Failover tự động AWS → Azure (watcher)

Cơ chế, quyết định, quyền AWS và vận hành chi tiết nằm ở [Failover tự động bằng watcher](AUTOMATION_FAILOVER.md). Phần này chỉ gồm các bước triển khai và diễn tập.

#### 1. Trước khi bật

1. Đã diễn tập GĐ6A (mục 9.1): fence, promote, rejoin và failback thủ công thành công, AWS lại là primary và Azure lại là standby streaming.
2. Bốn cờ đã bật và đã apply: `enable_workload_automation`, `enable_https`, `enable_health_checks`, `enable_failover`.
3. Tạo IAM user bằng Terraform (bước 2 bên dưới), rồi **tạo access key trong Console** cho user `dcs29-azure-watcher` và lưu thành file INI riêng, ngoài repo:

   ```ini
   [default]
   aws_access_key_id = ...
   aws_secret_access_key = ...
   ```

#### 2. Apply (hai bước)

Bước cài watcher cần file key, nên tạo IAM user trước, lấy key rồi mới cài. Nếu apply đầy đủ khi chưa có file key, Terraform vẫn tạo user nhưng dừng lỗi ở bước cài watcher (`Watcher AWS credentials missing`); chạy lại sau khi có key là được.

```powershell
# terraform.tfvars: enable_auto_failover = true
terraform plan  -target=aws_iam_user.azure_watcher -target=aws_iam_user_policy.azure_watcher
terraform apply -target=aws_iam_user.azure_watcher -target=aws_iam_user_policy.azure_watcher
# Console -> IAM -> dcs29-azure-watcher -> tạo access key, lưu vào file INI ngoài repo (xem bước 1)

# terraform.tfvars: azure_watcher_credentials_file = "C:/path/outside/repo/watcher-credentials.ini"
terraform plan
terraform apply
```

Terraform cảnh báo về `-target`; đây là lần dùng có chủ đích để tách việc tạo quyền khỏi việc cài dịch vụ. Plan của lượt thứ hai chỉ được thêm `terraform_data.watcher`; không replace VM, disk hay PVC. Bước cài đặt chạy trên laptop qua SSH: kiểm tra Azure là standby streaming, cài `python3-boto3`, copy ba tệp watcher, ghi cấu hình và credential, chạy `cloud_watcher.py --check`, rồi bật dịch vụ.

#### 3. Kiểm tra sau khi bật

```bash
sudo systemctl status dcs29-watcher
sudo journalctl -u dcs29-watcher -n 20     # mỗi chu kỳ: web=True db=True verdict=healthy
```

#### 4. Diễn tập

1. Bật monitoring (mục 10) và chạy `scripts/probe_failover.py` từ laptop tới `https://app.<domain>`.
2. Ghi mốc UTC, rồi Stop đúng EC2 trong Console. Không promote Azure bằng tay song song với watcher.
3. Theo dõi `journalctl -u dcs29-watcher -f`: phát hiện, fence (`stopped`), promote, Azure writable.
4. Kiểm tra `/api/database/status` và `/health/database` của Azure, AWS vẫn Stopped, shared domain trả về Azure sau khi DNS cập nhật.
5. Ghi detection time, failover time, downtime và số ghi bị mất theo [đo đạc failover](../../DO_DAC_FAILOVER.md). Thời gian trên cloud còn gồm chu kỳ health check Route 53 và TTL; không coi interval 30 giây là RTO 30 giây.

#### 5. Lỗi, bảo trì và failback

- Watcher thoát với mã 2 (không tự thử lại): xem `journalctl`, xử lý nguyên nhân (EC2 không dừng được, replay chưa đủ, Azure không ghi được), rồi quyết định promote thủ công theo 9.1.
- Bảo trì có kế hoạch (stop/reboot VM, thử lỗi pod, rejoin, failback): `sudo touch /opt/dcs29/watcher.pause` trước, xóa file sau khi xong.
- Không Start lại AWS cũ sau failover. Rejoin và failback theo 9.1; sau khi AWS lại là primary và Azure lại là standby, xóa `/opt/dcs29/failover-incident.json` và `sudo systemctl restart dcs29-watcher` để cài lại.

#### 6. Chi phí và dọn dẹp

Watcher không tạo dịch vụ AWS tính phí theo giờ: chỉ có gọi API (`DescribeInstances`, `GetHealthCheckStatus`) mỗi chu kỳ, trong mức miễn phí hoặc không đáng kể, và health check Route 53 vốn đã có ở GĐ6. Khi dọn lab: `sudo systemctl disable --now dcs29-watcher` trên VM Azure, rồi `terraform destroy` (user và access key bị xóa nhờ `force_destroy`). Credential và tệp watcher trên VM sẽ mất khi VM bị hủy; nếu VM được giữ, xóa `/etc/dcs29/watcher-aws-credentials` thủ công.

## 10. Giám sát và nghiệm thu

Monitoring và phép đo RTO/RPO chưa được Terraform triển khai. Các lệnh cài exporter, cấu hình Prometheus/Grafana và tạo workload thử nghiệm trong mục này vẫn cần người vận hành thực hiện.

### 10.1. Cài monitoring và theo dõi pods

Quy trình giám sát không phụ thuộc cách triển khai, nên dùng chung hướng dẫn của nhánh thủ công thay vì chép lại: **[GĐ7 — Monitoring và observability độc lập](../thu_cong/GIAI_DOAN_07_OBSERVABILITY.md)**. Làm các mục 7.1–7.4, 7.7 và 7.10; mục 7.8 (tự tạo panel) và 7.11–7.12 (pod, scale, VM và database metrics) là phần mở rộng.

Khác biệt khi đã triển khai bằng Terraform:

- Hai VM đã chạy sau apply: **bỏ qua 7.5 (thử khi VM tắt) và 7.6 (bật lại VM)**; không stop/start VM chỉ để cài monitoring.
- Hostname probe lấy từ `terraform output origin_hosts`. Trong `observability/prometheus.yml` sửa ba URL `/health/live` (job `blackbox_https`) và hai URL `/health/database` (job `blackbox_writer`).
- Grafana đã có dashboard `DCS29 - Failover` được nạp sẵn, nên chỉ tạo panel riêng khi cần.
- `/health/live` chỉ đo process/TLS. Muốn biết origin nào đang là writer thì xem job `blackbox_writer`: standby khỏe trả 503 nên `probe_success=0` ở đó là đúng.
- Nếu đã cài watcher, tạo `/opt/dcs29/watcher.pause` trên VM Azure trước khi stop/reboot VM hoặc thử lỗi pod (mục 9.2, bước 5).
- Khi GĐ7 nhắc runbook failover của GĐ6, dùng mục 9.1 của hướng dẫn này (bản cho Terraform).

Hoàn thành khi đạt checklist cuối GĐ7.

### 10.2. Kiểm thử và đo RTO/RPO

Dùng chung **[GĐ8 — Kiểm thử failover, phân tích và báo cáo](../thu_cong/GIAI_DOAN_08_KIEM_THU_FAILOVER.md)**. Cách đo và định nghĩa số liệu (detection time, failover time, downtime, số ghi bị mất) nằm ở [đo đạc failover](../../DO_DAC_FAILOVER.md).

Khác biệt khi dùng Terraform:

- Điền manifest từ cấu hình đang áp dụng: `terraform output dns_failover_timing` (chu kỳ × ngưỡng health check và TTL), cùng `watcher_threshold` và `watcher_interval_seconds` nếu chạy chế độ watcher.
- Giữ nguyên image, TTL, health check và replica giữa các lượt so sánh. Đổi một biến thì ghi vào manifest và coi là nhóm số đo khác.
- Chế độ thủ công (mục 9.1) và tự động bằng watcher (mục 9.2) không trộn trong cùng nhóm số đo.
- Probe và tải chạy ngoài AWS: `scripts/probe_failover.py`, hoặc script PowerShell ở GĐ8 mục 15–16.
- Khi GĐ8 nhắc runbook GĐ6, dùng mục 9.1 (thủ công) hoặc 9.2 (watcher) của hướng dẫn này.
- Sau thí nghiệm dọn lab theo mục 12.

## 12. Cập nhật image, bảo vệ state và dọn lab

Khi automation bật và vẫn ở trạng thái AWS primary/Azure standby, đổi `image_digest` trong tfvars rồi plan/apply. Terraform thay `terraform_data.workloads`, script apply Deployment mới trên cả hai VM và kiểm chứng cùng commit/schema/replication. VM không bị tạo lại chỉ vì đổi app digest. Đây chưa phải migration schema phiên bản mới; nếu release cần thay schema, phải có kế hoạch migration riêng.

Không đổi `postgres_image` để nâng major: automation chỉ chấp nhận PostgreSQL 17, kiểm tra lại image Debian/UID 999. Thay K3s release không tự nâng cluster đã tồn tại vì user_data/custom_data được ignore; cần backup và quy trình nâng cấp riêng.

Nếu đang ở trạng thái sau failover, không chạy lại automation initial deployment để cập nhật app; thực hiện cập nhật có kiểm soát theo role hiện tại và hoàn tất failback/rejoin trước.

Nếu bật GĐ6B, trước bảo trì/update/rejoin/destroy hãy tạo `/opt/dcs29/watcher.pause` trên VM Azure (mục 9.2, bước 5). Không dùng `enable_auto_failover=false` như nút pause. Khi dọn lab, dừng dịch vụ watcher theo mục 9.2, bước 6 trước destroy.

Trước dọn lab: export evidence, sao lưu PostgreSQL ra ngoài VM, xác định disk/PVC đang dùng. Xóa access key Certbot Azure đã tạo thủ công trước khi xóa IAM user; cập nhật nameserver tại registrar nếu không dùng zone nữa. Từ PowerShell laptop, ở `deploy/terraform`, xem kỹ danh sách tài nguyên sẽ bị xóa:

```powershell
terraform plan -destroy -out=destroy.tfplan
```

Chỉ sau khi đã sao lưu và quyết định dọn đúng lab, thực hiện:

```powershell
terraform apply destroy.tfplan
```

Không xóa state để dọn tài nguyên. Stop VM vẫn có thể phát sinh phí disk, IP, hosted zone và health checks. Không dùng `docker compose down -v` cho monitoring khi cần giữ số liệu.

---

## 13. Runbook sau triển khai đến demo

Phần này nối các bước đã có thành một lượt thực hiện cụ thể. Dùng cho **hai VM mới do Terraform và workload automation của repo triển khai**, database `issues`, tài khoản quản trị PostgreSQL `postgres`, namespace `issues`. Không dùng các lệnh chuyển role cho một hệ thống khác hoặc database tự cấu hình.

Demo cơ bản gồm: AWS phục vụ → tạo issue và kiểm tra replication → dừng AWS → Azure phục vụ và nhận dữ liệu mới → AWS rejoin → failback → Azure trở lại standby. **Rejoin/failback vẫn thủ công kể cả khi đã bật failover tự động.** Không chạy `terraform apply` để khôi phục role database sau sự cố.

### 13.1. Sau apply: hoàn thành các lượt triển khai còn lại

Nếu mới xong lượt 1, hệ thống chỉ có HTTP trên hai IP; chưa sẵn sàng demo qua domain chung. Làm lần lượt:

1. Kiểm tra AWS primary/writable và Azure standby/streaming theo mục 7.1.
2. Làm mục 8.1–8.4: delegate domain, chuẩn bị quyền DNS Azure, bật HTTPS và kiểm tra certificate/redirect.
3. Làm mục 9: bật health checks trước, xác nhận AWS Healthy/Azure Unhealthy, rồi bật DNS failover bằng lượt apply riêng.
4. Làm mục 10.1: sửa các URL probe (ba `/health/live`, hai `/health/database`) sang domain thật, bật monitoring trên laptop, tạo dashboard. Nếu hai VM đang chạy sau apply thì **bỏ qua bước thử khi VM tắt và bước Start VM**; không stop/start chỉ để cài monitoring.
5. Diễn tập thủ công đầy đủ mục 13 này trước. Chỉ khi đã failback và trở lại baseline mới cân nhắc bật GĐ6B bằng mục 9.2. Đây là lựa chọn thêm; demo thủ công không cần lượt apply GĐ6B.

**Kết quả cần có:** HTTPS hai origin và shared domain hoạt động; cùng commit ứng dụng; một writer AWS và một standby Azure; Grafana có dữ liệu probe. Apply thành công một lượt chưa thay thế các kiểm tra trên.

### 13.2. LAPTOP — mở phiên điều khiển và lấy đúng địa chỉ

Mở PowerShell tại thư mục gốc repo. Chạy từng lệnh; dùng phiên này suốt lượt demo:

```powershell
$RepoPath = (Get-Location).Path
Set-Location (Join-Path $RepoPath 'deploy/terraform')
$Origins = terraform output -json origin_hosts | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Không đọc được output Terraform; kiểm tra thư mục/state' }
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
$AwsUrl = "https://$($Origins.aws)"
$AzureUrl = "https://$($Origins.azure)"
$SharedUrl = "https://$($Origins.shared)"
$SshKey = 'C:/Users/YOUR_USER/.ssh/dcs29_terraform'
$AzureAdmin = 'azureuser'
```

Thay `$SshKey` bằng private key đang dùng; `$AzureAdmin` phải đúng `azure_admin_username` trong tfvars. Không in private key hoặc credential. Nếu lệnh lấy IP lỗi, dừng và kiểm tra state; không dùng IP nhớ từ lần triển khai khác.

Tạo thư mục bằng chứng riêng trong thư mục tạm của laptop:

```powershell
$RunId = 'demo-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$EvidenceDir = Join-Path $env:TEMP "dcs29-$RunId"
New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
function Save-DemoEvent([string]$Event) {
    [pscustomobject]@{
        time_utc = (Get-Date).ToUniversalTime().ToString('o')
        event = $Event
    } | Export-Csv -LiteralPath (Join-Path $EvidenceDir 'incident.csv') -NoTypeInformation -Append -Encoding UTF8
}
Save-DemoEvent 'begin_baseline_checks'
```

Giữ `$EvidenceDir` để sao chép ra nơi lưu bài báo cáo sau demo. Chỉ dùng dữ liệu demo; file API lưu author/nội dung issue, không đưa credential vào nội dung.

### 13.3. LAPTOP — xác nhận baseline trước khi gây lỗi

```powershell
$AwsVersion = Invoke-RestMethod "$AwsUrl/version"
$AzureVersion = Invoke-RestMethod "$AzureUrl/version"
$AwsDb = Invoke-RestMethod "$AwsUrl/api/database/status"
$AzureDb = Invoke-RestMethod "$AzureUrl/api/database/status"
$SharedVersion = Invoke-RestMethod "$SharedUrl/version"
if ($AwsVersion.commit_sha -ne $AzureVersion.commit_sha) { throw 'Hai origin khác commit' }
if ($AwsVersion.environment -ne 'aws' -or $AzureVersion.environment -ne 'azure') { throw 'Origin không đúng environment' }
if ($AwsDb.role -ne 'primary' -or -not $AwsDb.writable) { throw 'AWS chưa là writer' }
if ($AzureDb.role -ne 'standby' -or $AzureDb.writable -or -not $AzureDb.replication_connected) { throw 'Azure chưa là standby streaming' }
if ($SharedVersion.environment -ne 'aws') { throw 'Shared domain chưa phục vụ AWS; kiểm tra DNS/cache' }
Invoke-RestMethod "$AwsUrl/health/database"
curl.exe --silent --show-error --output NUL --write-out '%{http_code}' "$AzureUrl/health/database"
```

Lệnh cuối dự kiến in **503**, vì standby không được phép ghi; đó không phải lỗi triển khai. Nếu in `000`, kết nối/TLS lỗi; nếu `200`, kiểm tra lại role và dừng demo. Kiểm tra Route 53 Console: AWS writer health **Healthy**, Azure writer health **Unhealthy**.

Lưu baseline:

```powershell
@{ aws_version = $AwsVersion; azure_version = $AzureVersion; aws_database = $AwsDb; azure_database = $AzureDb; shared_version = $SharedVersion } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceDir 'baseline.json') -Encoding UTF8
Save-DemoEvent 'baseline_verified'
```

**Mở sẵn trên trình duyệt:** `$SharedUrl/issues.html`, `$AzureUrl/issues.html`, Grafana `http://localhost:3000` và Route 53 Health checks. Nếu demo tự động, mở thêm một terminal chạy `journalctl -u dcs29-watcher -f` trên VM Azure. Trong Grafana chọn khoảng thời gian 15–30 phút gần nhất và refresh phù hợp với scrape 10 giây. Trước sự cố, `probe_success{job="blackbox_https"}` của cả ba URL `/health/live` phải là 1; chỉ số này đo process/HTTPS, không chứng minh database ghi được.

Không tiếp tục nếu baseline sai. Nếu VM vừa reboot, xử lý guard theo mục 7.3 chỉ khi biết chắc chưa có failover; nếu writer đã đổi, dùng mục 9.1 để recovery.

### 13.4. LAPTOP — tạo dữ liệu AWS và xác nhận Azure đã nhận

Tạo issue qua **shared domain**, giữ request ID để retry đúng cùng nội dung:

```powershell
$CreateRequestId = [guid]::NewGuid().ToString()
$CreateBody = @{
    request_id = $CreateRequestId
    title = "Terraform demo $RunId"
    description = 'Tạo trên AWS trước sự cố'
    author = 'Demo User'
    status = 'open'
    version = 0
} | ConvertTo-Json
$Issue = Invoke-RestMethod -Method Post -Uri "$SharedUrl/api/issues" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($CreateBody))
$IssueId = $Issue.id
$CreateBody | Set-Content -LiteralPath (Join-Path $EvidenceDir 'create-request.json') -Encoding UTF8
$Issue | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceDir 'create-result.json') -Encoding UTF8
Save-DemoEvent 'issue_create_ack_aws'
```

Đọc chi tiết trên Azure sau vài giây; nếu 404 có thể replication chưa replay, chờ rồi gọi lại:

```powershell
$BeforeAws = Invoke-RestMethod "$AwsUrl/api/issues/$IssueId"
$BeforeAzure = Invoke-RestMethod "$AzureUrl/api/issues/$IssueId"
$BeforeAzure | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $EvidenceDir 'before-azure.json') -Encoding UTF8
$BeforeAws.issue
$BeforeAzure.issue
```

Hai bản phải cùng ID/title/version; lịch sử có `created`. Trên website chung mở issue vừa tạo, thêm một bình luận có nội dung `Trước sự cố - RUN_ID` và xác nhận bình luận/lịch sử xuất hiện khi tải lại chi tiết trên Azure. Lưu lại bằng lệnh dưới sau khi bình luận đã replicate:

```powershell
Invoke-RestMethod "$AzureUrl/api/issues/$IssueId" | ConvertTo-Json -Depth 12 |
    Set-Content -LiteralPath (Join-Path $EvidenceDir 'before-azure.json') -Encoding UTF8
Save-DemoEvent 'pre_failure_data_verified_on_azure'
```

Không gây lỗi khi dữ liệu demo chưa đến Azure. Có thể gõ thêm một bản nháp chưa gửi trên website chung để minh họa localStorage; bản nháp này không được tính vào dữ liệu PostgreSQL đã commit.

### 13.5. LAPTOP — chạy probe ghi CSV trong cửa sổ riêng

> **Khuyến nghị:** dùng `scripts/probe_failover.py` thay cho script PowerShell dưới đây. Nó mỗi giây gọi `GET /version` và `POST /api/ping`, ghi `probe.csv`, và khi kết thúc tự tính downtime đọc/ghi, tỷ lệ lỗi, p50/p95 và số lần ghi đã ACK nhưng không còn trên writer mới (xem [đo đạc failover](../../DO_DAC_FAILOVER.md)). Script PowerShell chỉ đọc `/version` và giữ lại cho máy không có Python.

Mở **PowerShell thứ hai**, điền hai giá trị bằng `$SharedUrl` và `$EvidenceDir` từ phiên điều khiển. Chạy trước khi gây lỗi, dừng bằng Ctrl+C sau khi failback hoàn tất:

```powershell
$ProbeUrl = 'https://app.YOUR_DOMAIN/version'
$ProbeCsv = 'COPY_EVIDENCE_DIR_HERE/probe.csv'
while ($true) {
    $Started = (Get-Date).ToUniversalTime().ToString('o')
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Status = 0
    $Environment = ''
    $ErrorText = ''
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $ProbeUrl -TimeoutSec 5 -ErrorAction Stop
        $Status = [int]$Response.StatusCode
        $Environment = ($Response.Content | ConvertFrom-Json).environment
    } catch {
        $ErrorText = $_.Exception.Message
        if ($_.Exception.Response) { $Status = [int]$_.Exception.Response.StatusCode }
    }
    $Watch.Stop()
    [pscustomobject]@{
        started_at_utc = $Started
        completed_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        status = $Status
        environment = $Environment
        duration_ms = $Watch.ElapsedMilliseconds
        error = $ErrorText
    } | Export-Csv -LiteralPath $ProbeCsv -NoTypeInformation -Append -Encoding UTF8
    Start-Sleep -Seconds 1
}
```

Dự kiến ban đầu các dòng status 200/environment aws. Mỗi vòng gồm thời gian request **cộng** một giây nghỉ, nên khi timeout không phải mẫu đúng mỗi giây. Probe `/version` đo tuyến và ứng dụng; RTO ghi phải lấy từ giao dịch ghi thành công ở bước 13.7. Đây không phải script tải Issues và không đo toàn bộ RPO; phép đo nâng cao làm theo mục 10.2.

### 13.6. Gây sự cố — chọn đúng một chế độ

**A. Demo thủ công (`enable_auto_failover=false`)**

1. Trong phiên điều khiển chạy `Save-DemoEvent 'aws_stop_requested'` ngay trước thao tác Stop.
2. AWS Console → EC2 → Instances → chọn đúng VM Terraform theo Elastic IP `$AwsIp`, đối chiếu instance ID/name → **Stop instance**. Không Terminate. Đợi **Stopped**, ghi instance ID và chụp trạng thái; chạy `Save-DemoEvent 'aws_stopped_verified'` khi đã xác nhận.
3. Đây là mô phỏng mất VM bất ngờ, nên không chạy drain trước Stop; replication bất đồng bộ có thể mất giao dịch cuối. Muốn diễn tập chuyển đổi có kế hoạch, làm drain/WAL tại mục 9.1, bước 4.1 **trước** khi Stop.
4. Mở phiên **SSH Azure riêng** từ PowerShell, dùng giá trị đã lấy ở phiên điều khiển:

   ```powershell
   ssh -i $SshKey "$AzureAdmin@$AzureIp"
   ```

5. Trong **SSH Azure**, xác nhận vẫn là standby và WAL đã nhận được replay:

   ```bash
   sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c "SELECT pg_is_in_recovery(), pg_is_wal_replay_paused(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(), pg_last_wal_replay_lsn() >= pg_last_wal_receive_lsn() AS received_wal_replayed;"
   ```

   Cần recovery=true, replay_paused=false, received_wal_replayed=true. Nếu NULL/false hoặc lệnh lỗi, dừng và điều tra; không promote ngay. Điều này chỉ chứng minh WAL **đã nhận** được replay, không chứng minh AWS đã gửi hết WAL.

6. Chỉ sau fencing đã xác nhận, trong **SSH Azure**:

   ```bash
   sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c 'SELECT pg_promote(true,30);'
   sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c 'SELECT pg_is_in_recovery();'
   ```

   Promote phải trả true và recovery phải false. Sau đó mới mở gate:

   ```bash
   sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=true WHERE singleton=true; COMMIT;"
   ```

7. Trở về phiên điều khiển laptop, chạy `Invoke-RestMethod "$AzureUrl/health/database"`; phải primary/writable=true và HTTP 200. Ghi `Save-DemoEvent 'azure_writer_verified'`. Không tự sửa record `app`: Route 53 sẽ chọn Azure khi health check cập nhật.

**B. Demo tự động (GĐ6B đã được nghiệm thu và bật)**

1. Trước Stop, kiểm tra baseline, `systemctl status dcs29-watcher` là active, không có `watcher.pause`, và đã xóa `failover-incident.json` theo mục 9.2 nếu có lượt cũ. Không bật tự động lần đầu ngay trước buổi trình bày.
2. Thực hiện Stop đúng EC2 và ghi hai mốc thời gian như chế độ A.
3. Theo dõi `journalctl -u dcs29-watcher -f` theo mục 9.2, bước 4. **Không chạy lệnh promote/gate thủ công của chế độ A** trong khi watcher còn hoạt động.
4. Sau khi watcher báo Azure là writer, kiểm tra Azure `/api/database/status`, `/health/database`, AWS Stopped và shared domain. Nếu watcher thoát mã 2, kiểm tra `journalctl` và role thực tế theo mục 9.2, bước 5 trước thao tác khác; không suy đoán promotion chưa xảy ra.

Ở cả hai chế độ, Grafana AWS process probe sẽ về 0; Azure process probe vẫn 1; shared có thể lỗi rồi về 1. Website giữ cùng URL. DNS/cache và thời gian điều phối có thể làm chuyển đổi mất vài phút; không coi 30 giây của health check là RTO.

### 13.7. LAPTOP — chứng minh Azure nhận ghi và retry không trùng

Trong phiên điều khiển, gọi qua DNS thật; nếu còn lỗi/cache thì chờ rồi gọi lại, không dùng `--resolve` để tuyên bố shared domain đã failover:

```powershell
$AfterVersion = Invoke-RestMethod "$SharedUrl/version"
$AfterDb = Invoke-RestMethod "$SharedUrl/api/database/status"
if ($AfterVersion.environment -ne 'azure' -or $AfterDb.role -ne 'primary' -or -not $AfterDb.writable) { throw 'Shared domain chưa phục vụ writer Azure' }
Invoke-RestMethod "$SharedUrl/api/issues/$IssueId"
Save-DemoEvent 'shared_azure_verified'
```

Tạo bình luận sau sự cố, rồi gửi lại **cùng request ID và body**:

```powershell
$CommentRequestId = [guid]::NewGuid().ToString()
$CommentBody = @{ request_id = $CommentRequestId; author = 'Demo User'; body = "Ghi trên Azure sau sự cố - $RunId" } | ConvertTo-Json
$CommentBytes = [Text.Encoding]::UTF8.GetBytes($CommentBody)
$Comment = Invoke-RestMethod -Method Post -Uri "$SharedUrl/api/issues/$IssueId/comments" -ContentType 'application/json; charset=utf-8' -Body $CommentBytes
Save-DemoEvent 'first_shared_write_ack_azure'
$RetryComment = Invoke-RestMethod -Method Post -Uri "$SharedUrl/api/issues/$IssueId/comments" -ContentType 'application/json; charset=utf-8' -Body $CommentBytes
if ($Comment.id -ne $RetryComment.id) { throw 'Retry trả comment ID khác' }
$AfterAzure = Invoke-RestMethod "$SharedUrl/api/issues/$IssueId"
if (@($AfterAzure.comments | Where-Object { $_.id -eq $Comment.id }).Count -ne 1) { throw 'Bình luận thiếu hoặc bị trùng' }
$AfterAzure | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $EvidenceDir 'after-azure.json') -Encoding UTF8
Save-DemoEvent 'azure_write_and_retry_verified'
```

Nếu POST đầu lỗi/timeout, kết quả có thể đã commit: giữ `$CommentBytes` và request ID, retry đúng body; không tạo ID mới. Mốc ACK chỉ là thời điểm laptop nhận phản hồi, không phải timestamp commit chính xác.

Trên website chung tải lại issue, chỉ rõ bình luận trước/sau sự cố và history. Có thể sửa, đóng rồi mở lại issue để trình bày nghiệp vụ; nếu làm vậy, lưu lại `after-azure.json` sau thao tác cuối để dùng đối chiếu khi rejoin. Bản nháp localStorage chỉ kiểm tra trên cùng browser/origin.

### 13.8. Recovery — rejoin AWS trước khi failback

Phần này cần nhiều thao tác; **diễn tập trước và dành thời gian riêng**, không bấm Start AWS rồi trông chờ DNS tự khôi phục dữ liệu.

1. Nếu dùng tự động, làm mục 9.2, bước 5: tạo `watcher.pause`. Không tiếp tục khi chưa rõ role thực tế của hai database. Không đổi flag Terraform để pause watcher.
2. Ghi `Save-DemoEvent 'aws_rejoin_begin'`. AWS Console → Start đúng EC2 đã fence. Guard phải giữ K3s dừng; mở SSH AWS bằng `ssh -i $SshKey "ubuntu@$AwsIp"`. **Không touch marker phê duyệt restart lúc này.**
3. Làm đầy đủ mục 9.1, bước **8.1–8.3 trên AWS**: xác minh primary cũ dừng → tạo PVC mới → clone từ Azure `10.200.29.2` → cập nhật manifest bằng K3s không agent → bật database/app sau khi workload đã trỏ storage mới. Dùng đúng PostgreSQL digest hiện tại. Các bước YAML/Python có sẵn ở mục đó; không bỏ qua hoặc apply manifest primary cũ.
4. Trong phiên điều khiển laptop:

   ```powershell
   $RejoinedAwsDb = Invoke-RestMethod "$AwsUrl/api/database/status"
   if ($RejoinedAwsDb.role -ne 'standby' -or $RejoinedAwsDb.writable -or -not $RejoinedAwsDb.replication_connected) { throw 'AWS chưa rejoin thành standby streaming' }
   $RejoinedIssue = Invoke-RestMethod "$AwsUrl/api/issues/$IssueId"
   $CurrentAzureIssue = Invoke-RestMethod "$AzureUrl/api/issues/$IssueId"
   $RejoinedIssue | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $EvidenceDir 'rejoined-aws.json') -Encoding UTF8
   if (@($RejoinedIssue.comments | Where-Object { $_.id -eq $Comment.id }).Count -ne 1) { throw 'AWS chưa nhận bình luận mới trên Azure' }
   if ($RejoinedIssue.issue.version -ne $CurrentAzureIssue.issue.version) { throw 'Version chưa khớp; chờ replay và đối chiếu lại' }
   Save-DemoEvent 'aws_standby_and_new_data_verified'
   ```

5. Đối chiếu thêm title/status, toàn bộ comment IDs và history với Azure. AWS `/health/database` lúc này phải 503; shared domain vẫn phục vụ Azure. **Chưa failback khi thiếu dữ liệu hoặc streaming chưa ổn định.**

### 13.9. Recovery — failback AWS và dựng lại Azure standby

1. Ghi `Save-DemoEvent 'failback_begin'`. Dừng thao tác ghi của người demo và workload tạo tải nếu có.
2. Làm mục 9.1, bước **4.1 trên Azure primary** với `DB_ADMIN_USER=postgres`, `APP_DATABASE=issues`: khóa ghi/drain, lấy WAL cuối; kiểm tra replay **trên AWS standby** với đúng LSN vừa lấy. Phải caught_up=true. Không lấy WAL trên nhầm primary.
3. Azure Portal → đúng VM có public IP `$AzureIp` → Stop; xác nhận **Stopped/Deallocated**. Chỉ sau fencing này mới promote **AWS**, kiểm tra recovery=false và mở gate bằng các lệnh mục 9.1, bước 4.3, đổi đích SSH sang AWS.
4. Kiểm tra `$AwsUrl/health/database` trả 200; chờ shared DNS trở về AWS. Trong phiên điều khiển:

   ```powershell
   $BackVersion = Invoke-RestMethod "$SharedUrl/version"
   $BackDb = Invoke-RestMethod "$SharedUrl/api/database/status"
   if ($BackVersion.environment -ne 'aws' -or $BackDb.role -ne 'primary' -or -not $BackDb.writable) { throw 'Shared domain chưa trở về writer AWS' }
   Invoke-RestMethod "$SharedUrl/api/issues/$IssueId" | ConvertTo-Json -Depth 12 |
       Set-Content -LiteralPath (Join-Path $EvidenceDir 'after-failback.json') -Encoding UTF8
   Save-DemoEvent 'shared_aws_writer_verified'
   ```

5. Start Azure với guard giữ K3s dừng; làm lại mục 9.1, bước **8.1–8.3 trên Azure**, source AWS `10.200.29.1`, PVC mới. Không bật lại PostgreSQL Azure primary trên storage cũ. Khi thao tác failback đã hoàn tất, Azure phải là standby streaming.
6. Gửi một bình luận mới qua shared domain/website sau failback, xác nhận xuất hiện trên Azure. Dùng request ID mới cho **nội dung mới**; kiểm tra lại role, commit, ID/version/comments/history và chỉ một writer như bước 13.3.
7. Nếu dùng tự động, chỉ rearm (xóa `failover-incident.json`, restart watcher) theo mục 9.2, bước 5 sau khi topology đã được xác minh. Nếu chưa định chạy lại tự động, giữ `watcher.pause` và ghi trạng thái đó trong biên bản; không đánh dấu hệ thống sẵn sàng demo tự động.

**Kết quả hoàn thành:** AWS primary/writable, Azure standby/streaming/read-only; shared URL AWS; dữ liệu tạo trên Azure vẫn tồn tại trên cả hai bên; không có hai writer. AWS writer check Healthy, Azure writer check Unhealthy, còn ba process probes `/health/live` về 1.

### 13.10. Lưu bằng chứng và checklist trước buổi trình bày

1. Ghi `Save-DemoEvent 'demo_cycle_complete'`, dừng probe bằng Ctrl+C. Giữ `incident.csv`, `probe.csv` và các file JSON. Lưu ảnh Grafana cả giai đoạn AWS → Azure → AWS, trạng thái fencing, writer health và issue/history. Không chụp Secret/credential.
2. Nếu dùng GĐ6B, lưu log `journalctl -u dcs29-watcher` và timeline của lượt chạy. Ghi rõ chế độ thủ công hay tự động; không dùng kết quả thủ công để tuyên bố tự động đã pass.
3. Trong timeline, phân biệt thời điểm Stop yêu cầu, VM Stopped, origin được quan sát lỗi, shared Azure và ACK ghi đầu tiên. CSV `/version` không tự chứng minh RPO; muốn báo RPO phải đối chiếu giao dịch ACK/receipt theo mục 10.2. Các bước demo này chứng minh dữ liệu mẫu, không thay phép đo tải hay tối thiểu ba lượt nghiệm thu.
4. Sao chép thư mục bằng chứng khỏi thư mục tạm, export dashboard JSON và chuẩn bị video của một lượt thành công. Nếu cần dọn lab sau cùng, làm mục 12 sau khi sao lưu database; không destroy ngay trước buổi demo.

Trước buổi demo, kiểm tra:

- [ ] Hai VM chạy, đúng image/commit, AWS primary/Azure standby streaming.
- [ ] HTTPS/shared URL hoạt động, health checks đúng vai trò.
- [ ] Monitoring chạy trên laptop, đúng domain, Grafana có dữ liệu gần nhất.
- [ ] SSH hai VM dùng được; đã diễn tập rejoin/failback với release K3s pin hiện tại.
- [ ] Đã chọn một chế độ demo; watcher đúng trạng thái (active, không pause, không còn incident cũ) nếu dùng tự động.
- [ ] Có bản sao lưu ngoài VM, đủ dung lượng PVC mới và bằng chứng/video dự phòng.
- [ ] Laptop không sleep, mạng ổn định; không đổi cấu hình/image trong lúc trình bày.

**Nếu demo dừng giữa chừng:** ghi writer/phase thực tế, giữ bằng chứng và storage cũ. Không khởi động writer cũ để cứu màn hình. Dùng bảng lỗi ở mục 9.1 hoặc mục 9.2 nếu dùng watcher; chỉ tiếp tục khi fencing và role của hai database đã được xác định.

## Khi gặp lỗi, reboot hoặc chuyển VM cũ

Chỉ đọc mục ứng với tình huống của bạn. Đây là các thao tác có điều kiện, không phải chuỗi lệnh cần chạy sau mỗi apply thành công.

### 5.1. Ngoại lệ: VM cũ từ Docker Compose, chưa được bootstrap K3s

**Chỉ khi chuyển VM cũ sang K3s — các bước migration**

**Bỏ qua toàn bộ mục này với VM mới do Terraform tạo.** Các lệnh dưới chỉ dành cho migration VM cũ; Terraform không tự migration dữ liệu hoặc chạy lại cloud-init trên VM đã tồn tại.

Terraform giữ `ignore_changes` cho AWS AMI/user_data và Azure custom_data nên **không tự chuyển VM cũ sang K3s** khi sửa tfvars. Resize VM có thể ngắt dịch vụ; xem plan và sao lưu trước. Không giảm disk, xóa volume hoặc chạy uninstall K3s để thử lại.

Với VM cũ, lưu cấu hình Compose/TLS và sao lưu database, rồi cài K3s bằng phiên bản đã pin như block dưới. Kiểm tra app qua port-forward trước, sau đó dừng NGINX cũ để giải phóng 80/443 và chuyển Ingress. Giữ volume cũ để rollback; không chạy đồng thời hai primary. Không chạy lại toàn bộ user_data trên VM đã có K3s vì có thể ghi đè cấu hình hiện tại.


Ví dụ cài K3s trên VM cũ sau khi sao lưu và đã kiểm tra kế hoạch chuyển đổi (SSH từng VM, thay release/node đúng bên):

```bash
sudo install -d -m 700 /etc/rancher/k3s
sudo nano /etc/rancher/k3s/config.yaml
```

Nội dung file; Azure đổi `issues-aws` thành `issues-azure`:

```yaml
write-kubeconfig-mode: "0600"
node-name: issues-aws
```

```bash
curl --fail --show-error --location --retry 5 https://get.k3s.io -o /tmp/install-k3s.sh
sudo env INSTALL_K3S_VERSION='K3S_RELEASE_DA_CHON' sh /tmp/install-k3s.sh
sudo k3s kubectl get nodes
sudo k3s kubectl create namespace issues --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Không ghi đè config nếu K3s đã tồn tại. Automation có bộ manifest riêng nên không cần file app từ bootstrap cũ. Database/WireGuard đã cấu hình thủ công phải được đánh giá migration riêng; script từ chối tự nhận database không thuộc automation. Khi NGINX cũ còn giữ 80/443, ServiceLB chưa thể phục vụ Ingress; chuyển cổng chỉ sau khi app/database được kiểm chứng qua port-forward.


### 7.2. Nếu automation thất bại hoặc cần chạy lại

**Khi apply lỗi hoặc cần retry — lệnh kiểm tra và chạy lại**

1. Chỉ thực hiện mục này khi apply lỗi hoặc cần chạy lại. Giữ Terraform state, VM và PVC. Xác định phase cuối từ output apply, SSH vào đúng VM từ PowerShell laptop:

```powershell
ssh -i "$env:USERPROFILE/.ssh/dcs29_terraform" "ubuntu@$AwsIp"
# Hoặc Azure; dùng username đã chọn trong azure_admin_username.
ssh -i "$env:USERPROFILE/.ssh/dcs29_terraform" "azureuser@$AzureIp"
```

Hai lệnh là hai phiên riêng; thoát phiên AWS bằng `exit` trước khi SSH Azure. Trong SSH của VM bị lỗi, đọc events/log:

```bash
sudo tail -n 100 /var/log/cloud-init-output.log
sudo journalctl -u k3s --no-pager -n 100
sudo k3s kubectl -n issues get pods,pvc
sudo k3s kubectl -n issues get events --sort-by=.lastTimestamp
sudo k3s kubectl -n issues logs postgres-0 -c base-backup --tail=100
```

2. Sửa đúng lỗi: SSH key/agent, CIDR laptop, image digest/public package, cổng NGINX cũ, tunnel hoặc dung lượng. Không đổi password khi database đã khởi tạo.
3. Nếu **lần clone Azure ban đầu chưa từng được xác minh** và PVC chứa bản copy dở, đổi `standby_storage_generation = "retry1"` trong tfvars. Script tạo PVC mới, giữ PVC cũ để kiểm tra; không xóa dữ liệu dở tự động. Một standby đã nghiệm thu nhưng offline phải dùng runbook recovery, không dùng thủ thuật này.
4. Terraform thường đánh dấu resource provisioner lỗi để chạy lại. Nếu cần yêu cầu chạy lại có chủ đích, trên **PowerShell laptop**:

```powershell
terraform plan -replace='terraform_data.workloads[0]' -out=retry.tfplan
# Đọc plan: chỉ thay resource orchestration; không replace VM/disk.
terraform apply retry.tfplan
```

Script giữ key/password/PVC và request ID cũ, bootstrap schema idempotent, kiểm tra role trước khi thay đổi. Nếu AWS đã thành standby hoặc Azure được promote, script **dừng**, không đảo failover/failback. Với database/WireGuard thủ công đã tồn tại, cần migration có kế hoạch; không xóa ownership check để chạy tiếp.


### 7.3. Sau khi reboot VM

**Chỉ sau reboot — kiểm tra writer và startup guard**

Automation cài guard K3s: marker `/run/issues-k3s-start-approved` mất sau reboot, vì vậy **K3s không tự chạy lại database cũ**. WireGuard có thể chạy nhưng database chưa được khởi động. Đây là bước vận hành vẫn cần người xác nhận.

Bỏ qua mục này trong lần triển khai mới; guard đã được automation cài sẵn. Chỉ trong lần restart bình thường, khi **đã xác nhận không có failover/promote và role trước khi tắt là AWS primary/Azure standby**, thực hiện trong SSH từng VM:

```bash
sudo touch /run/issues-k3s-start-approved
sudo systemctl start k3s
sudo k3s kubectl -n issues get pods
```

Sau đó kiểm tra API role/streaming như mục 7.1. Nếu Azure đã promote hoặc không xác định được writer, giữ K3s trên AWS cũ dừng và làm rejoin ở mục 9.1. Không touch marker/start K3s chỉ để automation tiếp tục.


### 8.5. Retry, credential rotation và phạm vi

**Khi HTTPS lỗi hoặc đổi credential — hướng dẫn**

Nếu DNS/CAA/credential hoặc Ingress lỗi, sửa đúng nguyên nhân và giữ state/certificate/Secrets. Chỉ khi HTTPS lỗi, SSH vào VM tương ứng để xem log:

```powershell
ssh -i "$env:USERPROFILE/.ssh/dcs29_terraform" "ubuntu@$AwsIp"
# Hoặc Azure; thay username nếu đã đổi azure_admin_username.
ssh -i "$env:USERPROFILE/.ssh/dcs29_terraform" "azureuser@$AzureIp"
```

Hai lệnh là hai phiên riêng, thoát phiên trước bằng `exit`. Trong SSH của VM bị lỗi:

```bash
sudo journalctl -u certbot.service --no-pager -n 100
sudo tail -n 100 /var/log/letsencrypt/letsencrypt.log
sudo k3s kubectl -n issues get ingress,middleware
```

Không force-renew liên tục để tránh rate limit. Script cố bật lại lịch renewal cho cả hai VM kể cả khi một phase thất bại.

Từ **PowerShell laptop**, sau khi sửa hoặc rotate credential:

```powershell
terraform plan -replace='terraform_data.https[0]' -out=https-retry.tfplan
terraform apply https-retry.tfplan
```

Đọc plan để bảo đảm không replace VM/disk. `enable_https=false` chỉ bỏ orchestration khỏi Terraform, **không tự gỡ certificate, Ingress hoặc timer đã cài**; không dùng biến này như nút tắt HTTPS. Chưa có cơ chế tự rollback TLS sau mọi lỗi.

Không tự đăng ký domain/đổi NS tại registrar, tạo secret access key, promote database, gỡ startup guard hoặc bật DNS failover. Sau HTTPS, chuyển sang mục 9 để bật health check rồi publish failover theo hai lượt. Health check phụ thuộc HTTPS resource khi bật để không tạo trước khi TLS automation hoàn tất.


## 11. Phần tự động hóa có thể bổ sung sau

**Tham khảo — các phần có thể tự động hóa tiếp**

WireGuard, PostgreSQL/PVC, schema, standby, Secret/app và HTTP Ingress đã được triển khai. TLS/renewal đã có automation opt-in. Failover AWS → Azure đã có opt-in ở mục 9.2. Các phần có thể bổ sung tiếp là backup ngoài VM, monitoring, rejoin và failback có kiểm chứng fencing; những phần này vẫn chưa tự động hóa.

Terraform có thể quản lý thêm tài nguyên và triển khai công cụ điều phối. Quy trình failover/failback database lúc sự cố nên do script hoặc watcher riêng xử lý với fencing và kiểm tra trạng thái; không coi một lần `terraform apply` là thao tác promote an toàn.

[Quay lại nhánh tự động](README.md) · [Lộ trình chung](../README.md).
