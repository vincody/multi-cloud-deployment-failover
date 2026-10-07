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
| Lượt 5: tùy chọn | [Bật failover tự động GĐ6B](#92-gđ6b--failover-tự-động-aws--azure), sau diễn tập GĐ6A, về baseline và có monitoring |
| Chuẩn bị và thực hiện demo | [Runbook sau triển khai đến demo](#13-runbook-sau-triển-khai-đến-demo): kiểm tra baseline, tạo dữ liệu, gây lỗi, xác nhận Azure, rejoin/failback và lưu bằng chứng |
| Vận hành | [Update image, bảo vệ state, dọn lab](#12-cập-nhật-image-bảo-vệ-state-và-dọn-lab); migration/retry/reboot ở cuối file |

**Bắt buộc:** [chuẩn bị công cụ/tài khoản](#2-chuẩn-bị-trên-laptop), thay placeholder, đọc plan trước apply và kiểm tra kết quả từng lượt. Giữ laptop/network hoạt động trong automation. Apply tạo tài nguyên có phát sinh phí.

**Cách đọc:** phần chính là luồng triển khai VM mới. Các mục tham khảo, hướng dẫn vận hành hoặc bước tùy chọn được trình bày trực tiếp bên dưới; đọc khi cần. Toàn bộ lệnh/manifest vẫn nằm trong file này. Giữ số mục gốc để các tham chiếu cũ tiếp tục dùng được.

**Điểm dừng:** apply lỗi thì giữ state/VM/PVC và dùng [mục 7.2](#72-nếu-automation-thất-bại-hoặc-cần-chạy-lại); TLS lỗi dùng [mục 8.5](#85-retry-credential-rotation-và-phạm-vi). Không promote khi tạo health check, không chạy promote tay song song với workflow tự động. Khi bảo trì/rejoin, làm theo điều kiện dừng controller của mục 9.2.

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
| Failover tự động GĐ6B | CloudWatch → EventBridge → Step Functions/Lambda, SSM Azure và incident latch khi `enable_auto_failover=true` | Diễn tập GĐ6A trước; rejoin/failback/rearm vẫn thủ công |
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

Các lệnh dưới thuộc **vận hành khi có sự cố hoặc diễn tập**, không phải bước cấu hình sau apply. Khi `enable_auto_failover=false`, fencing/promote thực hiện thủ công. GĐ6B ở mục 9.2 có thể tự fence AWS và promote Azure; rejoin/failback vẫn thủ công. Không chạy lệnh promote tay song song với workflow đang hoạt động.

Chỉ thực hiện các lệnh chuyển role sau khi baseline và DNS phần 9 đã được cấu hình. Không promote trong lúc tạo health checks.

#### 4. Chuyển role database — trước khi thử DNS failover

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


### 9.2. GĐ6B — Failover tự động AWS → Azure

**Tùy chọn, sau GĐ6A — các bước bật controller và vận hành**

Mặc định `enable_auto_failover=false`. Chỉ bật sau khi hoàn tất GĐ6A và trở về baseline. Terraform triển khai bộ điều phối; không promote trong apply.

#### 1. Phạm vi và trình tự

| Bước | Thành phần | Điều kiện/hành động |
|---|---|---|
| 1 | Route 53 | Kiểm tra `/health/database` AWS; interval 30 giây, threshold 3 hiện có |
| 2 | CloudWatch | `HealthCheckStatus < 1` trong 2 datapoint 60 giây; missing data không kích hoạt |
| 3 | EventBridge | Chỉ nhận ALARM của alarm `dcs29-auto-failover`, bắt đầu workflow |
| 4 | Lambda/DynamoDB | Ghi latch độc quyền cho incident; sự kiện trùng không tiếp tục |
| 5 | Lambda | Alarm vẫn ALARM và probe độc lập xác nhận AWS không sẵn sàng; kiểm tra lại sau 20 giây |
| 6 | SSM Azure | K3s đang chạy, PostgreSQL 17 là standby, replay không pause và đã replay WAL nhận được |
| 7 | Lambda/EC2 | Yêu cầu Stop EC2, poll tới đúng trạng thái `stopped`; không dùng force-stop |
| 8 | Lambda/SSM | Kiểm tra EC2 vẫn stopped, chạy promote trên Azure qua Run Command |
| 9 | PostgreSQL | Ghi incident receipt trước promote, promote và mở writer gate, kiểm tra lại role |
| 10 | Lambda | Public HTTPS Azure báo primary/writable và EC2 vẫn stopped |
| 11 | Route 53 | Writer health Azure thành Healthy, DNS failover chọn Azure; client còn cache cần thời gian |

Step Functions thực hiện các bước chờ và polling hữu hạn, timeout toàn workflow 25 phút. Lambda không giữ SSH key, không kết nối 5432 public và không tự khởi động K3s bị guard. Gửi SSM command không tự retry khi chưa biết command trước đã thực thi chưa; promotion cùng incident có receipt để tránh promote lặp.

#### 2. Region và quyền

CloudWatch metric của Route 53 được đọc ở **us-east-1**, nên alarm, EventBridge, Step Functions, Lambda, latch và SSM Azure nằm ở đó. EC2 vẫn dùng `aws_region` của project (ví dụ ap-southeast-1). Control plane ở ngoài VM AWS; với region chính khác us-east-1, nó cũng ở khác Region.

Đây là automation cho sự cố VM/app trong điều kiện API EC2 vẫn truy cập được. **Nếu cả Region AWS mất khả dụng và không thể xác nhận EC2 đã dừng, workflow dừng trước promote.** Nó không đảm bảo DR tự động toàn Region khi không còn bằng chứng fencing.

Azure được đăng ký thành SSM hybrid/multicloud node; không cần Azure client secret hoặc SSH runtime từ Lambda. Agent gọi endpoint SSM/ssmmessages us-east-1 qua outbound HTTPS 443. Không mở thêm inbound 22/5432/6443 cho Lambda. Registration dùng activation một máy, hết hạn sau một giờ và được xóa sau đăng ký; activation code chỉ đi qua bộ nhớ/SSH stdin, không nằm trong Terraform state.

User chạy Terraform cần AWS CLI đăng nhập đúng **cùng account Terraform** và thêm quyền (bao gồm `sts:GetCallerIdentity` để đối chiếu account):

- Tạo IAM role/policy, Lambda, Step Functions, CloudWatch/EventBridge và DynamoDB trong us-east-1.
- `ssm:CreateActivation`, `DeleteActivation`, `DescribeInstanceInformation`, `AddTagsToResource`, `PutParameter`.
- `iam:PassRole` cho role `dcs29-azure-ssm` khi tạo activation.

Lambda chỉ được Stop đúng EC2, gửi command đến SSM node gắn tag Project=dcs29/Purpose=azure-dr, đọc node parameter và quản lý incident của project. Lambda **không có DeleteItem để tự xóa latch**, không có StartInstances, không cập nhật DNS trực tiếp và không tự failback.

#### 3. Trước khi bật

Hoàn tất bốn lượt Terraform hạ tầng/app, HTTPS, health check, DNS. Làm GĐ6A: fencing/promote/rejoin/failback thủ công, rồi trả hệ thống về baseline:

- AWS primary/writable, Azure standby/streaming, cùng app commit.
- Hai public origin HTTPS hoạt động, startup guard có trên cả hai VM.
- AWS writer health Healthy, Azure standby writer health Unhealthy là đúng.
- Không còn diễn tập/command chuyển role đang chạy, có backup ngoài VM và inventory PVC active.

Terraform không biết buổi diễn tập đã pass; bạn phải xác nhận thực tế trước bật flag. Bootstrap kiểm tra lại role/gate/streaming và guard trước khi đăng ký agent, nhưng không thay thế nghiệm thu fencing/rejoin.

VM Azure có SSM registration/snap từ một hệ thống khác sẽ bị từ chối tự nhận; cần migration/adoption riêng. Không xóa agent identity để ép đăng ký lại. Trên VM mới của project, automation cài agent từ AWS và lưu marker `/opt/dcs29/ssm-dr.json` root-only.

#### 4. Lượt apply GĐ6B

Trong `deploy/terraform/terraform.tfvars` giữ các flag cũ và thêm:

```hcl
enable_workload_automation = true
enable_https               = true
enable_health_checks       = true
enable_failover            = true
enable_auto_failover       = true
```

Trên **PowerShell laptop**, thư mục `deploy/terraform`:

```powershell
terraform init
terraform fmt -check
terraform validate
terraform test
terraform plan -out=auto-failover.tfplan
# Đọc plan: thêm control resources/SSM; không replace VM, disk hoặc PVC active.
terraform apply auto-failover.tfplan
terraform output auto_failover
```

`terraform init` bổ sung provider archive để đóng gói Lambda. Bootstrap dùng Python/OpenSSH và AWS CLI trên laptop; không cần boto3 trên laptop để triển khai. Code Lambda dùng SDK có sẵn trong runtime.

Terraform tạo hybrid role, kiểm tra baseline, đăng ký/reuse agent Azure, chờ Online, gắn tag và ghi node ID vào Parameter Store Standard `/dcs29/dr/azure-node`. ID không phải credential. Resource `terraform_data.azure_ssm` không tự promote database trong apply. Sau đó tạo workflow/alarm/event target; từ thời điểm rule được bật, sự cố có thể tự dẫn tới Stop EC2/promote Azure.

Trong **AWS Console**, chọn Region us-east-1:

- Systems Manager → Managed nodes: `dcs29-azure-dr` Online.
- Parameter Store: `/dcs29/dr/azure-node` trỏ đúng node Azure.
- CloudWatch → Alarms: `dcs29-auto-failover` về OK sau đủ dữ liệu baseline.
- EventBridge → Rules: `dcs29-auto-failover` Enabled, target là workflow.
- Step Functions: state machine Standard `dcs29-auto-failover`, chưa có execution đang promote.

#### 5. Diễn tập và kiểm chứng

Bật monitoring/evidence trước. Khi đã cho phép gây sự cố, Stop đúng EC2 hoặc gây lỗi có kiểm soát; không promote Azure bằng tay song song với workflow. Theo dõi execution trên Step Functions:

```powershell
$WorkflowArn = terraform output -json auto_failover | ConvertFrom-Json | Select-Object -ExpandProperty workflow
aws stepfunctions list-executions --state-machine-arn $WorkflowArn --region us-east-1
```

Sau completion, kiểm tra trên laptop, thay YOUR_DOMAIN thật:

```powershell
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/database
curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/issues
```

Azure primary/writable, EC2 stopped, shared URL về environment azure sau DNS cache; issue/comment/history đã replicate vẫn đọc được. Ghi giao dịch ACK và timeline/RTO/RPO theo GĐ8. AWS ALARM vẫn có thể tồn tại sau failover; latch chặn các incident trùng, không tự trả AWS về primary.

Thời gian gồm health check, alarm 2×60 giây, xác minh, preflight, EC2 stop, SSM, health check Azure và cache DNS. Không coi interval 30 giây là RTO 30 giây. Async replication không đảm bảo dữ liệu cuối đã commit trên AWS đều đến Azure; chờ replay chỉ đảm bảo xử lý WAL **đã nhận**, không xác nhận WAL chưa truyền.

#### 6. Lỗi, maintenance và failback

Nếu AWS hồi phục, DNS/TLS probe không đáng tin, Azure offline/replay pause, không Stop được EC2 hoặc SSM trả kết quả lỗi: workflow dừng, giữ latch. Trường hợp command timeout, promotion có thể đã xảy ra trước khi trả kết quả; kiểm tra SSM invocation và role thực tế, không gửi promote mới mù quáng.

Trước maintenance, rejoin hoặc failback, tắt **rule trigger**, không đặt `enable_auto_failover=false` chỉ để pause vì biến đó sẽ gỡ cả control resources:

```powershell
aws events disable-rule --name dcs29-auto-failover --region us-east-1
aws stepfunctions list-executions --state-machine-arn $WorkflowArn --status-filter RUNNING --region us-east-1
$AzureNode = aws ssm get-parameter --name /dcs29/dr/azure-node --query Parameter.Value --output text --region us-east-1
aws ssm list-commands --instance-id $AzureNode --region us-east-1
```

Disable rule không hủy execution/SSM command đã bắt đầu. Không Start AWS primary cũ khi còn execution/command Pending/InProgress/Delayed/Cancelling hoặc chưa xác định role. Giữ AWS bị fence; rejoin/failback bằng mục 9.1 ở trên, không dùng initial workload automation để ghi đè PVC active sau rejoin.

Sau toàn bộ command/execution cũ đã terminal và failback đã về AWS primary/Azure standby/streaming, mới rearm. Trong SSH Azure, chỉ xóa incident receipt khi database đã là standby; root file chứa owner ARN để đối chiếu:

```bash
test "$(sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -At -c 'SELECT pg_is_in_recovery()')" = t || exit 1
sudo cat /opt/dcs29/failover-incident.json
sudo rm -f /opt/dcs29/failover-incident.json
```

Không xóa marker khi Azure còn primary. Trên laptop, đọc latch bằng file JSON để tránh lỗi quoting trên Windows PowerShell:

```powershell
$LatchKeyFile = Join-Path $env:TEMP 'dcs29-dr-latch-key.json'
[IO.File]::WriteAllText($LatchKeyFile, '{"id":{"S":"writer"}}', [Text.UTF8Encoding]::new($false))
$Incident = aws dynamodb get-item --table-name dcs29-dr-latch --key "file://$LatchKeyFile" --consistent-read --region us-east-1 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $Incident.Item.owner.S) { throw 'Không đọc được incident owner' }
$Incident.Item
```

Chỉ khi roles/SSM commands đã được xác minh, xóa đúng latch với điều kiện owner không đổi:

```powershell
$OwnerFile = Join-Path $env:TEMP 'dcs29-dr-latch-owner.json'
$OwnerJson = @{ ':owner' = @{ S = $Incident.Item.owner.S } } | ConvertTo-Json -Depth 4 -Compress
[IO.File]::WriteAllText($OwnerFile, $OwnerJson, [Text.UTF8Encoding]::new($false))
aws dynamodb delete-item --table-name dcs29-dr-latch --key "file://$LatchKeyFile" --condition-expression 'owner = :owner' --expression-attribute-values "file://$OwnerFile" --region us-east-1
if ($LASTEXITCODE -ne 0) { throw 'Latch chưa được xóa; giữ rule disabled' }
Remove-Item -LiteralPath $LatchKeyFile, $OwnerFile
```

Kiểm tra CloudWatch alarm về OK trước bật lại rule:

```powershell
aws cloudwatch describe-alarms --alarm-names dcs29-auto-failover --query 'MetricAlarms[].StateValue' --region us-east-1
aws events enable-rule --name dcs29-auto-failover --region us-east-1
```

Terraform có thể sửa drift Enabled/Disabled của rule ở apply sau; không apply trong lúc đang maintenance/rejoin. Latch không có TTL và không tự mở lại, kể cả khi workflow fail; đây là bước xác nhận thủ công để tránh tự động chạy incident mới trên topology sau failover chưa được phục hồi.

#### 7. Chi phí và cleanup

Ngoài CloudWatch/EventBridge/Lambda/Step Functions còn có DynamoDB on-demand (vài record/request incident) và SSM hybrid. Run Command trên Azure hiện tính 0,002 USD/invocation; workflow gửi preflight/promote, polling không phải gửi command mới. Logs Lambda giữ 7 ngày. Không có NAT Gateway, Lambda không gắn VPC. Chi phí thực tế còn phụ thuộc region, hạn mức miễn phí, logs và số retry; không đảm bảo tổng bằng 0.

Khi dọn lab: disable rule, bảo đảm không còn execution/SSM command active, sao lưu DB/evidence. Deregister đúng hybrid node và xóa parameter do bootstrap tạo trước khi destroy:

```powershell
aws ssm deregister-managed-instance --instance-id $AzureNode --region us-east-1
aws ssm delete-parameter --name /dcs29/dr/azure-node --region us-east-1
```

SSM identity/agent trong VM và parameter tạo qua bootstrap không tự rollback khi bỏ resource Terraform. Không deregister để retry khi VM vẫn đang phục vụ; phục hồi registration có kế hoạch. Destroy latch/workflow không khôi phục role database hay thay rejoin/failback.


## 10. Giám sát và nghiệm thu

Monitoring và phép đo RTO/RPO chưa được Terraform triển khai. Các lệnh cài exporter, cấu hình Prometheus/Grafana và tạo workload thử nghiệm trong mục này vẫn cần người vận hành thực hiện.

### 10.1. Cài monitoring và theo dõi pods

### Giai đoạn 7 — Monitoring và observability độc lập

**Đầu vào:** GĐ5/GĐ6 đã pass với domain của bạn. Trước khi bật stack, thay **ba target URL trong `observability/prometheus.yml`** bằng hostname của bạn theo domain/IP ở phần 3; file trong repo chỉ là cấu hình của một lượt lab, không phải giá trị mặc định để dùng nguyên.
**Mục tiêu:** thu bằng chứng availability/latency từ laptop bên ngoài AWS để monitoring vẫn sống khi EC2 bị dừng.

> **Ghi chú ngay lúc này:** Bạn có thể để **cả EC2 và Azure VM tắt** trong lúc nghỉ. Bộ monitoring ở GD7 chạy trên **laptop Windows**, độc lập với hai VM. Khi hai VM tắt, cả ba probe sẽ báo lỗi; đó là kết quả đúng, không phải cấu hình hỏng. Chỉ bật hai VM ở bước 7.6 trước khi xác nhận hệ thống healthy và thử failover. Route 53 health checks/hosted zone vẫn tồn tại và có thể tiếp tục phát sinh phí dù máy đã tắt. Docker Desktop/Prometheus/Grafana trên laptop chỉ ghi số liệu khi laptop và Docker đang chạy.

> **Khi không cần monitoring:** Trên **PowerShell laptop** (không SSH vào EC2/Azure), đứng tại thư mục repo rồi dừng cả Prometheus, Grafana và Blackbox Exporter bằng các lệnh sau. Chỉ cần làm nếu bạn đã chạy GD7 và tạo `observability/.env`; nếu chưa khởi chạy ba container thì không có gì cần dừng.

```powershell
Set-Location 'PATH_TO_REPO'
docker compose --env-file observability/.env -f observability/compose.yaml stop
docker compose --env-file observability/.env -f observability/compose.yaml ps --all
```

> Lệnh `stop` giữ container và dữ liệu Prometheus/Grafana. Trong lúc dừng sẽ không có mẫu monitoring mới; Route 53 và hai VM không bị ảnh hưởng. Có thể đóng Docker Desktop sau đó. Khi cần dùng lại, mở Docker Desktop rồi chạy `docker compose --env-file observability/.env -f observability/compose.yaml up -d` từ cùng thư mục. **Không dùng `down -v`** nếu muốn giữ các volume dữ liệu.

**Cập nhật Issues/K3s:** giữ các bước Docker Desktop, Prometheus/Grafana và tạo panel dưới đây. Docker Compose ở chương này chạy trên **laptop monitor**, không trên hai VM app. HTTP liveness, database writer readiness và pod state là ba tín hiệu khác nhau. /health/database Azure standby khỏe trả 503, không dùng nó làm probe sống của Azure.

Trước mục 7.3, sửa ba URL target trong observability/prometheus.yml sang hostname thật với /health/live để đo process/TLS; cấu hình repo cũ còn /health/ready nên phải sửa rõ ràng. Liveness 200 không chứng minh DB/schema/ghi hoạt động; thêm kiểm tra DB/Issues riêng ở 7.11.

#### 0. Bản đồ thao tác — đọc trước khi làm

| Nhãn trong hướng dẫn | Thực hiện ở đâu |
|---|---|
| **LAPTOP / POWERSHELL** | Terminal PowerShell Windows mở tại thư mục repo của bạn, **không SSH** |
| **LAPTOP / TRÌNH DUYỆT** | Docker Desktop, Prometheus `http://localhost:9090`, Grafana `http://localhost:3000` |
| **AWS CONSOLE** | EC2 → Instances → Start/kiểm tra EC2 primary |
| **AZURE PORTAL** | Virtual machines → Start/kiểm tra Azure standby |
| **CHỈ EC2** | Lệnh Linux chạy trong phiên SSH vào AWS, chỉ dùng ở bước thử lỗi AWS |
| **CHỈ AZURE** | SSH để cài kube-state-metrics, port-forward hoặc kiểm tra app |

Không cài Prometheus/Grafana lên EC2 hoặc Azure VM. Laptop là điểm quan sát thứ ba: EC2 lỗi thì số liệu vẫn ghi nếu laptop còn hoạt động. Khi laptop tắt/sleep hoặc Internet laptop mất, xuất hiện khoảng trống dữ liệu; **không diễn giải khoảng trống đó thành downtime ứng dụng**.

Ba URL cần cấu hình cho lần chạy mới:

| Nhãn | URL probe | IP origin đã dùng ở GD5/GD6 |
|---|---|---|
| `aws-origin` | `https://aws-origin.YOUR_DOMAIN/health/live` | `AWS_PUBLIC_IP` |
| `azure-origin` | `https://azure-origin.YOUR_DOMAIN/health/live` | `AZURE_PUBLIC_IP` |
| `shared-domain` | `https://app.YOUR_DOMAIN/health/live` | Route 53 chọn AWS hoặc Azure |

**Quan trọng:** `aws-origin` và `azure-origin` là hostname riêng, không đi qua record failover `app`. Target `shared-domain` chỉ cho biết URL người dùng đang hoạt động; nó **không cho biết cloud nào đã trả lời**. Để xác định cloud, gọi `/version` và đọc `environment` như GD6/GD8.

#### Hướng dẫn thực hiện từ đầu đến cuối

##### 7.1. LAPTOP — kiểm tra Docker Desktop và vị trí repo

1. Mở **Docker Desktop** trên Windows. Đợi góc dưới/trạng thái hiện **Engine running**; chọn Linux containers nếu Docker đang ở Windows containers. Nếu chưa cài, làm theo [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) và bật WSL 2 theo hướng dẫn chính thức. Không cần cài Docker trên hai VM thêm lần nữa.
2. Mở **PowerShell mới** trên laptop; chạy từng lệnh:

   ```powershell
   Set-Location 'PATH_TO_REPO'
   docker --version
   docker compose version
   docker info --format '{{.ServerVersion}}'
   ```

   Lệnh cuối phải in phiên bản Docker Engine. Nếu báo `failed to connect to the docker API`, Docker Desktop chưa chạy xong; mở app và đợi rồi chạy lại. Các lệnh từ 7.1 đến 7.8 đều ở **PowerShell laptop**, trừ nơi có nhãn khác.

##### 7.2. LAPTOP — tạo mật khẩu Grafana local

Repo có sẵn `observability/compose.yaml`, `prometheus.yml`, `blackbox.yml` và Grafana datasource. **Không cần tạo lại YAML**, nhưng **phải sửa URL probe** vì file mẫu còn hostname của lượt triển khai trước. Trên PowerShell laptop:

```powershell
Copy-Item observability/.env.example observability/.env
notepad observability/.env
notepad observability/prometheus.yml
```

Trong `.env`, thay giá trị sau dấu `=` bằng mật khẩu riêng, lưu và đóng. Không thêm dấu nháy hoặc khoảng trắng ở hai đầu. File `.env` đã được `.gitignore`; không commit hoặc gửi lên chat. Nếu đã có `.env`, bỏ qua `Copy-Item` để tránh ghi đè mật khẩu.

Trong `prometheus.yml`, tìm **ba dòng `targets` dưới job `blackbox_https`** và thay URL thành `https://aws-origin.YOUR_DOMAIN/health/live`, `https://azure-origin.YOUR_DOMAIN/health/live`, `https://app.YOUR_DOMAIN/health/live` sau khi thay `YOUR_DOMAIN` bằng domain thật. Giữ nguyên label `target_name` tương ứng. Lưu file, đọc lại ba URL để chắc chắn không còn hostname/IP của người triển khai khác. Chỉ sau bước này mới chạy `docker compose ... up -d` ở 7.3.

##### 7.3. LAPTOP — bật stack monitoring

Chạy **từng lệnh** trong PowerShell laptop:

```powershell
docker compose --env-file observability/.env -f observability/compose.yaml config -q
docker compose --env-file observability/.env -f observability/compose.yaml pull
docker compose --env-file observability/.env -f observability/compose.yaml up -d
docker compose --env-file observability/.env -f observability/compose.yaml ps
```

Lệnh `config -q` không in gì khi hợp lệ. `pull` có thể mất vài phút lần đầu; cần Internet. `ps` cần cho thấy `blackbox`, `prometheus`, `grafana` đều `Up`. Các image dùng tag `latest` để dễ khởi chạy lab; **trước buổi đo chính thức GD8**, lưu version/digest đang chạy và không `pull` giữa các lượt so sánh. Dữ liệu nằm ở Docker volumes; `stop` hoặc `down` bình thường không xóa dữ liệu, còn `down -v` **sẽ xóa**, không chạy lệnh đó nếu muốn giữ số liệu.

Mở trên **trình duyệt laptop**:

- `http://localhost:9090` — giao diện Prometheus.
- `http://localhost:3000` — giao diện Grafana.

Hai cổng chỉ bind `127.0.0.1` của laptop. Không cần mở inbound 3000/9090 trong AWS Security Group hoặc Azure NSG. Không cần đụng Route 53 cho GD7.

##### 7.4. LAPTOP — hiểu ba file cấu hình trước khi xem số liệu

- `observability/blackbox.yml`: GET HTTPS `/health/live`, chỉ chấp nhận HTTP `200`, kiểm tra TLS certificate thật, dùng IPv4, timeout 7 giây. **Không dùng `curl -k` hoặc `insecure_skip_verify`**.
- `observability/prometheus.yml`: mỗi 10 giây gọi Blackbox `/probe` cho ba URL, timeout scrape 8 giây. Nhãn `target_name` giữ tên dễ đọc. `prometheus` tự scrape chính nó.
- `observability/compose.yaml`: retention 7 ngày; Grafana đọc Prometheus qua mạng Docker nội bộ `http://prometheus:9090`, nên trong Grafana **không nhập `localhost:9090`** làm datasource.

Xem log nếu container vừa khởi động xong mà UI chưa lên:

```powershell
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 prometheus
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 blackbox
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 grafana
```

##### 7.5. LAPTOP — kiểm tra Prometheus khi hai VM **vẫn đang tắt**

1. Trình duyệt laptop → `http://localhost:9090/targets`. `prometheus` cần **UP**. Ba dòng thuộc job `blackbox_https` thường cũng **UP** vì Prometheus gọi được **exporter**; trạng thái này **không chứng minh website healthy**.
2. Trình duyệt laptop → `http://localhost:9090/graph` (hoặc **Query**). Dán `probe_success{job="blackbox_https"}` vào ô query → **Execute** → chọn **Table**. Khi cả hai VM tắt, ba target dự kiến là `0`. Có thể thấy `0` sau tối đa vài chu kỳ 10 giây. Nếu không có dòng nào, kiểm tra container/log/network.
3. Thử thêm `probe_http_status_code{job="blackbox_https"}`. Khi không kết nối được, mã có thể là `0`; đừng hiểu đó là HTTP status do server trả về.

**Giải thích:** `up{job="blackbox_https"}=1` nghĩa là scrape từ Blackbox exporter thành công. `probe_success=1` mới nghĩa là endpoint đích trả đúng `200`, qua HTTPS với TLS hợp lệ. Nếu hai VM đang tắt thì `probe_success=0` là kết quả đúng.

##### 7.6. AWS CONSOLE + AZURE PORTAL — bật lại đúng hai origin

Đến bước này mới cần bật cloud. Nếu lượt trước đã failover sang Azure, không Start AWS với primary cũ tự khởi động: chặn workload/endpoint và rejoin theo runbook phần 9 trước. Các bước bật máy bên dưới dành cho trạng thái ổn định đã xác nhận chỉ một writer:

1. **AWS CONSOLE:** EC2 → Instances → chọn EC2 GD3 → **Start instance**. Đợi trạng thái `Running` và status checks pass. Elastic IP dự kiến vẫn là `AWS_PUBLIC_IP`.
2. **AZURE PORTAL:** Virtual machines → chọn VM GD4 → **Start**. Đợi `Running`. Static Public IP dự kiến vẫn là `AZURE_PUBLIC_IP`.
3. **LAPTOP / POWERSHELL:** chạy **từng lệnh**:

   ```powershell
   Resolve-DnsName aws-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   Resolve-DnsName azure-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
   ```

Hai DNS origin phải trả đúng IP trên; ba lệnh readiness phải thành công. `/version` ở trạng thái bình thường dự kiến `environment=aws`. Sau Start VM, guard sẽ giữ K3s dừng. Xác nhận đúng role và phê duyệt restart theo mục 7.3 trước khi kiểm tra curl; nếu vẫn chưa kết nối, kiểm tra K3s, Ingress và database ở phần 5, 7, 8, 9 trước khi sửa monitoring. Nếu IP đã thay đổi thì sửa bản ghi A origin và cập nhật tài liệu, không cố dùng IP cũ.

##### 7.7. LAPTOP — xác nhận probe và Grafana

1. Quay lại Prometheus → **Query** → `probe_success{job="blackbox_https"}` → **Execute**. Cả ba nhãn `target_name` phải về `1` sau vài chu kỳ; đợi thêm nếu VM mới khởi động. `probe_http_status_code` phải là `200` cho cả ba.
2. Query `probe_duration_seconds{job="blackbox_https"}`: mỗi dòng có độ trễ probe tính bằng giây. Query `probe_ssl_earliest_cert_expiry{job="blackbox_https"}`: nếu có dòng, đây là Unix timestamp hết hạn cert sớm nhất được quan sát. Dùng biểu thức `(probe_ssl_earliest_cert_expiry{job="blackbox_https"} - time()) / 86400` để xem số ngày còn lại. Nếu metric không xuất hiện, xem log probe và TLS trước khi kết luận certificate đã hết hạn.
3. Mở `http://localhost:3000` → đăng nhập username `admin`, password bạn đã đặt ở 7.2. Nếu Grafana hỏi đổi mật khẩu thì đặt mật khẩu mới và ghi nhớ; thay đổi này nằm trong volume Grafana. Lần sau sửa `.env` **không tự đổi** mật khẩu của user đã tạo trong volume.
4. Menu **Connections → Data sources** (hoặc **Configuration → Data sources** tùy phiên bản) → chọn **Prometheus**. URL phải là `http://prometheus:9090`; datasource này được tạo sẵn từ file provisioning và có thể ở chế độ chỉ đọc. Nếu có nút **Save & test/Test**, bấm thử. Nếu không có, mở **Explore**, chọn datasource **Prometheus**, chạy `probe_success{job="blackbox_https"}` để xác nhận có dữ liệu. Không tạo datasource thứ hai trùng tên.

##### 7.8. LAPTOP / GRAFANA — tạo dashboard theo từng panel

1. Grafana → **Dashboards → New → New dashboard → Add visualization** → chọn datasource **Prometheus**.
2. Tạo từng panel từ bảng dưới. Trong editor panel, chọn loại hiển thị; nhập PromQL ở ô query; đổi **Title**; **Apply/Back to dashboard**; lặp lại. Với panel có ba series, đặt legend `{{target_name}}` để phân biệt AWS/Azure/domain. Chọn time range `Last 30 minutes` và refresh `10s` ở góc dashboard. UI có thể thay đổi tên nút giữa các phiên bản.

   | Panel / loại | PromQL | Cách đọc |
   |---|---|---|
   | **Health ba target** / Time series hoặc State timeline | `probe_success{job="blackbox_https"}` | `1` healthy, `0` lỗi; legend `{{target_name}}` |
   | **HTTP status** / Time series | `probe_http_status_code{job="blackbox_https"}` | `200` thành công; `502/503` app/Service lỗi qua Traefik; `0` thường là chưa nhận HTTP response |
   | **Độ trễ probe** / Time series | `probe_duration_seconds{job="blackbox_https"}` | Giây cho toàn bộ probe từ laptop; không phải latency riêng trong FastAPI |
   | **Tỷ lệ probe lỗi 5 phút** / Time series | `1 - avg_over_time(probe_success{job="blackbox_https"}[5m])` | Tỷ lệ `0–1`; đặt Unit = Percent (0.0–1.0) |
   | **TLS còn hạn** / Time series hoặc Stat | `(probe_ssl_earliest_cert_expiry{job="blackbox_https"} - time()) / 86400` | Ngày còn lại; đặt Unit = days |

3. Bấm **Save dashboard**, đặt tên ví dụ `DCS29 - Multi-cloud external health`. Chọn timezone **UTC** trong dashboard settings nếu UI có, hoặc ghi rõ timezone đang hiển thị trong báo cáo. Mốc đo ở GD8 phải ghi UTC.
4. Để đánh dấu lần test: trong dashboard, tạo **Annotation** tại thời điểm bạn dừng/khôi phục app (tùy phiên bản Grafana: Ctrl+click vào biểu đồ, hoặc menu annotation của dashboard). Nội dung ngắn, ví dụ `t_action stop AWS app`, `t_detect Route53 unhealthy`, `t_first_B first azure-standby`, `t_stable shared healthy`, kèm UTC. Đây là mốc thao tác thủ công, không suy ra từ riêng đồ thị 10 giây.
5. Export dashboard JSON qua menu **Share/Export → Export as JSON** hoặc **Dashboard settings → JSON model** (vị trí tùy phiên bản), lưu trong `observability/grafana/dcs29-dashboard.json` nếu muốn version-control. Kiểm tra JSON không chứa password/token trước khi commit. Screenshot chỉ là hình minh họa; JSON + dữ liệu thô mới đủ tái kiểm tra.

**Lưu ý về app metrics:** `/metrics` trong FastAPI có `dcs29_http_requests_total` và `dcs29_http_request_duration_seconds`. Cấu hình GD7 **chưa scrape hai endpoint này** để không phụ thuộc vào việc mở metrics qua Internet. Do đó panel request rate và latency **bên trong app** chưa có dữ liệu. Đừng tạo panel rồi diễn giải `No data` thành ứng dụng không có request. Nếu cần các panel này ở giai đoạn sau, thiết kế đường truy cập bảo vệ `/metrics` (ví dụ VPN/SSH tunnel hoặc allowlist ổn định) rồi mới thêm scrape job; không thêm `https://.../metrics` công khai chỉ cho tiện demo. Hãy kiểm tra ingress Traefik hiện tại: nếu URL `/metrics` đang được truy cập từ Internet, endpoint đó đang public từ GD5 và nên được hạn chế trước khi triển khai thật.

##### 7.9. Smoke test pod — trên AWS K3s, không promote Azure

Khi app Deployment đã chạy, ghi UTC từ PowerShell và mở Grafana. Trên SSH AWS:

```bash
sudo k3s kubectl -n issues get pods -l app=issues-app
sudo k3s kubectl -n issues rollout restart deployment/issues-app
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
sudo k3s kubectl -n issues get pods -l app=issues-app
```

Quan sát pod cũ thay bằng pod mới và HTTP probe có thể gián đoạn ngắn nếu chỉ một replica. Đây là K3s hồi phục app, không phải failover cloud/DB. Không stop PostgreSQL hoặc promote Azure trong bài này.

Nếu muốn quan sát một vòng cloud failover, thực hiện đủ fencing/WAL/promote/rejoin/failback theo runbook phần 9 rồi ghi timeline ở Grafana. Không chỉ restart AWS và để DNS tự failback về DB cũ. Khi pod exporter/cluster không truy cập được, phân biệt mất scrape với pod Ready=0.

##### 7.10. LAPTOP — lưu dữ liệu, nghỉ và làm tiếp hôm sau

- Khi cần lưu kết quả thử: export JSON dashboard và ghi UTC của thao tác, các phiên bản container, scrape interval `10s`, retention `7d`, thời điểm laptop/Docker chạy. Tạo thư mục `experiments/runs/<ten-luot>/` nếu muốn lưu probe CSV và ảnh; thư mục này được Git ignore theo mặc định. GD8 sẽ quy định run ID và phép đo timeline chi tiết. **Không bắt buộc lưu screenshot để hoàn thành việc cài GD7**, nhưng phải giữ raw metrics hoặc khả năng query/export trước khi volume bị xóa/retention hết hạn.
- Ví dụ xuất **30 phút dữ liệu probe thô** từ Prometheus API trên PowerShell laptop (chạy sau khi đã có số liệu; đổi tên thư mục cho mỗi lần đo):

  ```powershell
  $RunDir = 'experiments/runs/gd7-smoke-01'
  New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
  $EndUnix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $StartUnix = $EndUnix - 1800
  $Query = [uri]::EscapeDataString('probe_success{job="blackbox_https"}')
  $Url = "http://localhost:9090/api/v1/query_range?query=$Query&start=$StartUnix&end=$EndUnix&step=10s"
  Invoke-RestMethod -Uri $Url | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 "$RunDir/probe-success.json"
  ```

  File JSON này lưu mẫu `probe_success` theo thời gian; nó **không chứa toàn bộ** status/latency/TLS. Lặp với biểu thức metric khác khi cần phân tích GD8. Nếu API trả lỗi hoặc file rỗng, kiểm tra URL Prometheus và time range trước khi kết luận mất dữ liệu.
- Xem image/digest đang chạy từ **PowerShell laptop**: `docker compose --env-file observability/.env -f observability/compose.yaml images` và `docker image inspect prom/prometheus:latest quay.io/prometheus/blackbox-exporter:latest grafana/grafana:latest --format '{{.RepoTags}} {{.Id}}'`.
- **Nghỉ hôm nay:** đóng browser nếu muốn; dừng monitoring bằng `docker compose --env-file observability/.env -f observability/compose.yaml stop`. Có thể stop EC2/Azure sau khi kết thúc thử như GD6. Ba probe sẽ không có mẫu mới khi stack/laptop dừng; lần sau `up -d` rồi bật VM để tiếp tục. Docker volume giữ dữ liệu cũ nếu không `down -v`/xóa Docker data.
- Nếu muốn để monitor ghi liên tục thì **không** stop stack/laptop; để laptop cắm nguồn, tắt sleep, giữ Internet. Nếu mục tiêu của bạn là nghỉ và tiết kiệm cloud thì dừng cả hai VM được; health check Route 53 lúc ấy sẽ unhealthy và đường `app` không khả dụng cho tới khi VM bật lại.

#### Giải thích tín hiệu và giới hạn phép đo

**Tham khảo — hiểu các tín hiệu monitoring**

| Tín hiệu | Ý nghĩa đúng |
|---|---|
| Prometheus target UP | Prometheus liên lạc được với Blackbox exporter; website đích có thể vẫn lỗi |
| probe_success = 1 | URL đích trả HTTP 200, HTTPS và certificate hợp lệ trong thời gian timeout |
| probe_success = 0 | URL đích không đạt điều kiện trên; xem status, log probe, DNS/TLS và trạng thái VM |
| shared-domain healthy | Người dùng mới có thể tới một origin healthy; không xác định được AWS hay Azure chỉ từ metric này |
| Khoảng trắng trên biểu đồ | Có thể do laptop/Docker/Prometheus ngừng chạy hoặc mất Internet; không tự coi là downtime website |

Khoảng lấy mẫu 10 giây không thể xác định thời điểm sự kiện chính xác tới từng giây. Route 53 có chu kỳ health check và DNS cache riêng; so sánh các timestamp ở GD8 với sai số lấy mẫu và cache. Ba probe không thay thế được log client hoặc phép đo k6. Monitor trên laptop không phải dịch vụ giám sát 24/7.


#### Khi gặp lỗi

**Khi monitoring lỗi — cách kiểm tra**

| Hiện tượng | Kiểm tra theo thứ tự |
|---|---|
| docker info không kết nối | Mở Docker Desktop, đợi Engine running, kiểm tra Linux containers/WSL 2 |
| Grafana localhost:3000 không mở | docker compose ps, logs grafana; cổng 3000 có bị ứng dụng khác dùng không |
| Prometheus target blackbox DOWN | Container blackbox, log Prometheus/Blackbox, file cấu hình và mạng Docker |
| Target UP nhưng probe_success = 0 | Đây là lỗi đích; kiểm tra hai VM có Running, curl HTTPS trên laptop, DNS, TLS, Traefik/app |
| AWS probe 0, Azure 1, shared 0 lâu | Kiểm tra Route 53 health check, record failover và DNS cache theo runbook phần 9 |
| HTTP status = 0 | Không nhận được HTTP response; xem DNS/TCP/TLS/timeout, không gọi đó là mã lỗi của app |
| SSL expiry không có dữ liệu | Xem probe có đi qua HTTPS thành công và metric exporter có được phát hay không |
| Dashboard No data | Kiểm tra query, time range, datasource và xem Prometheus có mẫu cùng khoảng thời gian |
| Sau nghỉ thấy gap | Kiểm tra laptop/Docker có dừng, VM có tắt; ghi rõ khoảng ngừng thu mẫu |

Nếu cần xem chi tiết lỗi một probe, mở trên laptop qua Prometheus UI để xem sample, hoặc dùng lệnh sau rồi tìm log của target. Blackbox không mở cổng trực tiếp ra host:

~~~powershell
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 100 blackbox
~~~


#### 7.11. Bổ sung giám sát pod, scale và database — từng bước

##### A. Triển khai kube-state-metrics trong từng cluster

Chọn version exporter tương thích K3s/Kubernetes, ghi KSM_VERSION. Xem [manifest standard chính thức](https://github.com/kubernetes/kube-state-metrics/tree/main/examples/standard), review RBAC/image trước apply. Trên SSH từng VM, thay KSM_VERSION bằng tag đã chọn; thư mục clone phải chưa tồn tại:

```bash
cd /opt/dcs29
sudo apt-get install -y git
git clone --depth 1 --branch KSM_VERSION https://github.com/kubernetes/kube-state-metrics.git kube-state-metrics
sudo k3s kubectl apply --dry-run=server -k /opt/dcs29/kube-state-metrics/examples/standard
sudo k3s kubectl apply -k /opt/dcs29/kube-state-metrics/examples/standard
sudo k3s kubectl -n kube-system get deployment,svc kube-state-metrics
```

Mẫu standard dùng namespace kube-system và Service metrics 8080; đối chiếu version đã clone trước chạy. Không mở exporter hoặc API 6443 cho toàn Internet.

##### B. Đưa metrics tới laptop qua kênh quản trị

Demo có thể dùng SSH tunnel. Giữ các terminal dưới đây mở trong lúc scrape.

**SSH AWS — terminal riêng:**

```bash
sudo k3s kubectl -n kube-system port-forward service/kube-state-metrics 19081:8080 --address 127.0.0.1
```

**SSH Azure — terminal riêng:**

```bash
sudo k3s kubectl -n kube-system port-forward service/kube-state-metrics 19082:8080 --address 127.0.0.1
```

**Laptop PowerShell — một terminal mỗi tunnel, thay key/user/IP:**

```powershell
ssh -N -i PATH_TO_AWS_PRIVATE_KEY -L 127.0.0.1:19081:127.0.0.1:19081 ubuntu@AWS_PUBLIC_IP
```

```powershell
ssh -N -i PATH_TO_AZURE_PRIVATE_KEY -L 127.0.0.1:19082:127.0.0.1:19082 AZURE_ADMIN_USER@AZURE_PUBLIC_IP
```

Terminal đứng chờ là bình thường. Trong terminal laptop khác:

```powershell
curl.exe --fail http://127.0.0.1:19081/metrics
curl.exe --fail http://127.0.0.1:19082/metrics
```

Không mở 19081/19082 trên SG/NSG. Tunnel/port-forward mất khi session hoặc VM dừng; reconnect trước khi kết luận exporter hỏng.

##### C. Thêm scrape jobs và kiểm tra từ container monitor

Trong observability/prometheus.yml, thêm dưới scrape_configs, cùng cấp với jobs hiện có:

```yaml
  - job_name: kube_state_aws
    static_configs:
      - targets: ['host.docker.internal:19081']
        labels:
          cluster: aws
  - job_name: kube_state_azure
    static_configs:
      - targets: ['host.docker.internal:19082']
        labels:
          cluster: azure
```

host.docker.internal dành cho Docker Desktop laptop, không phải DNS trên VM. Phải kiểm tra kết nối từ **container Prometheus**, không chỉ curl laptop; nếu target DOWN do đường từ Docker tới host/tunnel, xử lý route/binding qua kênh riêng hoặc cấu hình host networking được hỗ trợ trước khi tiếp tục. Không đổi SSH listener ra toàn Internet để chữa lỗi.

```powershell
docker compose --env-file observability/.env -f observability/compose.yaml config -q
docker compose --env-file observability/.env -f observability/compose.yaml restart prometheus
```

Prometheus → Targets: kube_state_aws/azure UP. Query thử metric dưới trước khi tạo panel; No data cần kiểm tra exporter/version/namespace và scrape.

##### D. Grafana panel pod/replica

Giữ cách tạo panel ở 7.8, thêm các query:

| Panel | PromQL | Legend gợi ý |
|---|---|---|
| Pod Ready | kube_pod_status_ready{namespace="issues",condition="true"} | cluster/pod |
| Restart container | kube_pod_container_status_restarts_total{namespace="issues"} | cluster/pod/container |
| Desired replicas | kube_deployment_spec_replicas{namespace="issues",deployment="issues-app"} | cluster |
| Available replicas | kube_deployment_status_replicas_available{namespace="issues",deployment="issues-app"} | cluster |

Trên AWS thử scale app (không scale DB):

```bash
sudo k3s kubectl -n issues scale deployment/issues-app --replicas=2
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
sudo k3s kubectl -n issues get pods -l app=issues-app
```

Grafana cập nhật sau scrape; kiểm tra CPU/RAM và cùng image/DSN. Sau thử giảm lại replicas=1. Hồi phục pod không bảo đảm tất cả giao dịch thành công, vẫn đối chiếu API/DB.

##### E. Database và VM metrics

Tiếp theo thêm node exporter/collector PostgreSQL qua đường quản trị bảo vệ, theo dõi disk, CPU/RAM, role, sender/receiver và WAL lag. /api/database/status hiện cho biết role/LSN nhưng không phải endpoint Prometheus; cần exporter/collector để chuyển thành metric, không thêm nó vào scrape_configs rồi mong tự parse JSON.

Ghi CPU credit từ cloud metrics khi tích hợp. Prometheus/Grafana không tự fence/promote PostgreSQL; tự động hóa cloud cần điều phối GĐ6. Hoàn tất GĐ7 khi pod fail/recover/scale và tín hiệu DB được kiểm chứng, không chỉ ba probe HTTP.

#### Điều kiện hoàn thành GD7

- [ ] Bộ ba container chạy trên laptop, Prometheus và Grafana mở được qua localhost.
- [ ] Khi hai VM bật, cả ba target có probe\_success = 1 và HTTP status = 200.
- [ ] Grafana có dashboard health, status, duration, tỷ lệ lỗi, TLS expiry; biết giải thích từng panel.
- [ ] Thử rollout restart app K3s, theo dõi probe và pod; không promote DB chỉ vì pod lỗi.
- [ ] Hồi phục pod và xác nhận probe, DB role và replication; cloud failback theo runbook phần 9.
- [ ] Ghi mốc UTC, phiên bản image, interval 10 giây, retention 7 ngày; export dashboard JSON và raw data khi làm buổi đo chính thức.
- [ ] Kiểm tra tình trạng public của /metrics trước khi định thêm scrape app metrics.

Cấu hình chuẩn bị xong chưa có nghĩa GD7 đã hoàn thành: bạn cần chạy các bước 7.1–7.9 khi có thời gian. Sau đó dùng dashboard/timeline này ở GD8 — kiểm thử failover.

Nguồn kỹ thuật: [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [Prometheus multi-target exporter](https://prometheus.io/docs/guides/multi-target-exporter/), [Blackbox exporter configuration](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).

### 10.2. Kiểm thử và đo RTO/RPO

**Khi nghiệm thu — workload, kịch bản thử và cách đo**




### Giai đoạn 8 — Kiểm thử failover, phân tích và báo cáo

**Đầu vào:** hoàn thành GĐ1–GĐ7 với thông số của bạn trong cấu hình phần 3. Chỉ dùng số đo, domain, digest và IP của chính lần thử này.
**Mục tiêu:** chạy thí nghiệm lặp lại được và tính detection time, failover time, downtime, error rate, latency và cost proxy.

#### 1. Điều kiện trước khi chạy

- AWS/Azure cùng digest, khác APP_ENV.
- Hai origin và shared HTTPS pass.
- Writer check AWS healthy, Azure standby check unhealthy đúng role; process/TLS probe hai origin healthy.
- Monitoring nằm ngoài AWS.
- Máy k6/probe không sleep, clock đồng bộ.
- Budget và cleanup owner được xác nhận.
- Run manifest ghi commit, digest, TTL, health interval/threshold, replica, client DNS mode và tải.

Không thay đổi image/TTL/replica giữa các lượt so sánh.

#### 2. Workload Issues và kiểm chứng dữ liệu

##### 2.1. Tạo issue/bình luận qua API — PowerShell laptop

Dùng dữ liệu demo và SHARED_HOST thật. Chạy khi DB đang là primary đã nghiệm thu:

```powershell
$BaseUrl = 'https://app.YOUR_DOMAIN'
$CreateRequestId = [guid]::NewGuid().ToString()
$CreateBody = @{request_id=$CreateRequestId; author='Demo'; title='Kiem thu AWS Azure'; description='Noi dung truoc failover'; status='open'; version=0} | ConvertTo-Json
$Issue = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/issues" -ContentType 'application/json' -Body $CreateBody
$IssueId = $Issue.id
$CommentRequestId = [guid]::NewGuid().ToString()
$CommentBody = @{request_id=$CommentRequestId; author='Demo'; body='Binh luan truoc failover'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/issues/$IssueId/comments" -ContentType 'application/json' -Body $CommentBody
Invoke-RestMethod "$BaseUrl/api/issues/$IssueId" | ConvertTo-Json -Depth 20
```

Ghi IssueId, request_id, version, response và UTC. Retry cùng thao tác dùng lại body/request_id; giao dịch mới phải có mã mới. Sửa issue dùng PUT /api/issues/{id} cùng version hiện tại, không đổi trạng thái bằng dữ liệu version cũ.

##### 2.2. Dữ liệu trong vòng chuyển đổi

1. Trước lỗi: đợi Azure replay và so đầy đủ issue/bình luận/lịch sử.
2. Gây lỗi VM, thực hiện fencing/promote theo runbook phần 9; ghi request đã ACK và request kết quả chưa rõ.
3. Trên Azure mới: bình luận/sửa/đóng issue, retry kiểm tra không trùng.
4. AWS rejoin: dữ liệu mới Azure phải có trên AWS trước failback.
5. Sau failback: so lại ID/version/comments/history/receipts và thử giao dịch mới trên AWS.
6. Giữ bản nháp chưa gửi trong browser cùng URL để kiểm tra localStorage riêng với transaction database.

##### 2.3. Tải và script cũ

Script tests/load/failover.js hiện gọi /api/devices, chỉ kiểm chứng workload dashboard/read-only. Có thể giữ phép đo HTTP 10 request/giây trong 10 phút để tham khảo, nhưng không lấy đó làm bằng chứng replication giao dịch Issues.

Kịch bản tải Issues phải bổ sung request_id riêng, version phù hợp, lưu ACK/receipt và đối chiếu dữ liệu; chưa có script này trong repo. Nếu không viết load script, vẫn làm đủ vòng nghiệp vụ/role/WAL phía trên và ghi phạm vi phép đo. Khi tăng tải theo dõi dropped_iterations, CPU credit và RAM 4 GiB.

#### 3. Hai chế độ DNS/client

##### Client mặc định

Giữ DNS/connection settings k6 mặc định, ghi rõ k6 version và resolver. Kết quả phản ánh client đó, không đại diện mọi browser.

##### DNS experiment

Chạy thêm cấu hình TTL nội bộ ngắn và noConnectionReuse để quan sát authoritative change rõ hơn. Resolver OS/network vẫn có cache. Không so latency giữa hai cấu hình rồi quy toàn bộ chênh lệch cho cloud.

#### 4. Probe song song

Mỗi giây gọi `/version` qua shared domain, lưu CSV:

```text
started_at_utc,completed_at_utc,status,environment,duration_ms,error
```

Song song query recursive resolver và authoritative nameserver. Việc này phân biệt Route 53 đã đổi record nhưng client còn cache.

#### 5. Kịch bản phải chạy

| Mã | Kịch bản | Tác động | Mục đích |
|---|---|---|---|
| B0 | Single-cloud baseline | Chỉ AWS | Chứng minh không có standby thì outage kéo dài |
| B1 | Multi-cloud normal | Cả hai healthy, AWS primary | Baseline latency/cost |
| F1 | App pod failure | Rollout/recreate pod app K3s | Kiểm tra hồi phục tại cluster, không mặc định promote DB |
| F2 | Environment failure chính | Stop EC2 AWS | Đáp ứng yêu cầu đề tài |
| F3 | Recovery/failback | AWS bị chặn writer cũ, rejoin rồi chuyển role | Đo trở về primary sau đồng bộ |
| F4 | Failover dưới tải | Tăng tải có kiểm soát rồi stop | Đo capacity/tail latency Azure |
| F5 | Readiness 503 | Process sống nhưng not ready | Kiểm tra decision signal |

F2 là bằng chứng chính; F5 không thay thế F2. Mỗi scenario tối thiểu 3 lượt khởi đầu, đợi process probes hai origin healthy, AWS primary/Azure standby streaming giữa các lượt.

#### 6. Quy trình một lượt F2

1. Tạo run ID, thư mục output và manifest.
2. Xác nhận version/digest/role/WAL và dữ liệu Issues hai origin.
3. Xác nhận writer checks đúng role, process probes và Grafana có dữ liệu.
4. Bắt đầu Prometheus/Grafana, probe CSV và k6.
5. Chạy baseline đủ 3 phút.
6. Ghi UTC gửi lệnh stop EC2 (`t_action`).
7. Ghi external origin down (`t0`).
8. Ghi Route 53 unhealthy (`t_detect`).
9. Xác nhận fencing, promote Azure và writer readiness; ghi response Azure đầu tiên qua shared domain (t_first_B) và giao dịch ghi đầu tiên.
10. Ghi 10 success liên tiếp (`t_stable`).
11. Tiếp tục đo Azure ổn định.
12. AWS trở lại trong trạng thái chặn primary cũ; rejoin, chờ dữ liệu/WAL rồi failback có kiểm soát theo runbook phần 9.
13. Dừng tải, export raw data/dashboard/log.
14. Xác nhận resources và chi phí; không dọn trước khi lưu evidence.

Lệnh stop/start chỉ dùng đúng instance lab đã xác minh bằng describe. Gửi stop không đồng nghĩa instance down ngay.

#### 7. Định nghĩa metric

| Chỉ số | Công thức/định nghĩa |
|---|---|
| Detection time | `t_detect - t0` |
| First-Azure failover | `t_first_B - t0` |
| Stable recovery | t_stable - t0, kèm xác minh giao dịch ghi hoạt động |
| RTO ghi | Thời điểm shared URL xác nhận giao dịch ghi thành công sau lỗi trừ t0 |
| RPO | Giao dịch primary cũ đã ACK commit nhưng không có trên primary mới; ghi số lượng và khoảng thời gian |
| Error rate | failed HTTP/network requests / total requests trong cửa sổ |
| Client downtime | tổng và khoảng lỗi liên tục dài nhất |
| Latency | p50/p95/p99 cho baseline, transition, Azure steady |
| Cost proxy | giờ chạy × vCPU/RAM/storage/IP/query/log/request |

Với probe 1 giây, độ phân giải xấp xỉ 1 giây cộng thời gian request. Không gọi first Azure là phục hồi hoàn toàn nếu request cũ vẫn lỗi.

#### 8. Tổ chức output

Mỗi run lưu:

- `manifest.yaml`: scenario/config/digest/TTL.
- `incident.csv`: event và UTC.
- `probe.csv`.
- `k6.json` hoặc summary.
- Prometheus/Grafana export.
- AWS/Azure/Route 53 snapshots.
- `analysis.md` nêu kết quả và anomaly.

Raw data là bất biến; tạo file tổng hợp riêng. Không chỉnh raw CSV để làm kết quả đẹp.

#### 9. Phân tích kết quả

Báo từng lượt, trung bình, min/max và outlier. Tách ba cửa sổ: baseline, transition, Azure steady. So B0 với F2 để cho thấy giá trị standby. So latency AWS/Azure nhưng ghi region/network/client. Báo cả request count thực tế và dropped iterations.

Kết luận cần trả lời:

- Failover mất bao lâu ở authoritative DNS và ở client?
- Bao nhiêu request lỗi?
- Azure có chịu được tải?
- Failback có gây lỗi lần hai?
- Chi phí warm standby là gì?
- Những giới hạn nào ngăn kiến trúc trở thành production HA?

#### 10. Lỗi/threats to validity

- DNS/OS/browser/k6 cache.
- HTTP keep-alive.
- Clock lệch.
- Chỉ chạy một lượt.
- Monitoring cùng failure domain.
- Cache nóng/lạnh không nhất quán.
- Tải phát không đủ.
- Region/Internet jitter.
- Thay config giữa các lượt.
- Dùng stop container để tuyên bố mất toàn environment.

Ghi các yếu tố này trong report thay vì giấu.

#### 11. Cleanup sau thí nghiệm

Export evidence trước. Dừng/xóa đúng resources theo inventory: EC2, EBS nếu không cần, Elastic IP/public IPv4, Route 53 checks/records, Azure VM/managed disk/NIC/Public IP/Resource Group, log retention. Không xóa hosted zone/domain/tài nguyên dùng chung nếu chưa xác nhận.

#### 12. Checklist nghiệm thu

- [ ] Có B0, B1, F2, F3; F2 chạy ít nhất 3 lượt.
- [ ] Probe/k6/monitor chạy ngoài AWS.
- [ ] Timeline đủ `t_action/t0/t_detect/t_first_B/t_stable`.
- [ ] Có error rate/downtime/latency, RTO ghi và RPO thực tế.
- [ ] Có issue/comments/history/receipt trước-sau, không hai writer.
- [ ] AWS nhận đủ dữ liệu Azure trước failback; standby streaming lại cuối vòng.
- [ ] Có raw data và run manifest.
- [ ] Có DNS authoritative vs recursive evidence.
- [ ] Có cost proxy.
- [ ] Phân tích outlier/limitations.
- [ ] Có runbook recovery/cleanup.
- [ ] Có video hoặc dữ liệu dự phòng cho demo.

#### 13. Kết quả mong đợi

Sản phẩm cuối không chỉ là website đổi từ AWS sang Azure, mà là bộ bằng chứng định lượng cho thấy failover diễn ra thế nào, mất bao lâu, lỗi bao nhiêu, tốn gì và giới hạn ở đâu.

#### 14. Kiểm chứng storage và giới hạn

Sau nghiệm thu primary/standby/dữ liệu mới, kiểm tra inventory PV/path/DB cũ và dung lượng. Chỉ dọn đúng bản cũ không phục vụ; không xóa PVC active hoặc prune cả cụm. Async replication có thể mất giao dịch cuối khi VM sập. Request chưa phản hồi cần kiểm tra receipt trước khi kết luận mất; bản nháp chưa save không tính là giao dịch mất.

Các mốc read-only/DNS của script cũ giữ để đối chiếu, nhưng báo cáo hiện hành phải tách chúng khỏi RTO/RPO của Issues/PostgreSQL. Không đánh dấu cloud đã pass từ kết quả local.

## 12. Cập nhật image, bảo vệ state và dọn lab

Khi automation bật và vẫn ở trạng thái AWS primary/Azure standby, đổi `image_digest` trong tfvars rồi plan/apply. Terraform thay `terraform_data.workloads`, script apply Deployment mới trên cả hai VM và kiểm chứng cùng commit/schema/replication. VM không bị tạo lại chỉ vì đổi app digest. Đây chưa phải migration schema phiên bản mới; nếu release cần thay schema, phải có kế hoạch migration riêng.

Không đổi `postgres_image` để nâng major: automation chỉ chấp nhận PostgreSQL 17, kiểm tra lại image Debian/UID 999. Thay K3s release không tự nâng cluster đã tồn tại vì user_data/custom_data được ignore; cần backup và quy trình nâng cấp riêng.

Nếu đang ở trạng thái sau failover, không chạy lại automation initial deployment để cập nhật app; thực hiện cập nhật có kiểm soát theo role hiện tại và hoàn tất failback/rejoin trước.

Nếu bật GĐ6B, trước bảo trì/update/rejoin/destroy hãy disable EventBridge rule, xác nhận mọi execution/SSM command đã terminal theo mục 9.2. Không dùng `enable_auto_failover=false` như nút pause. Khi dọn lab, deregister đúng hybrid node và xóa parameter theo mục 9.2 trước destroy.

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
4. Làm mục 10.1: sửa ba URL probe sang domain thật và `/health/live`, bật monitoring trên laptop, tạo dashboard. Nếu hai VM đang chạy sau apply thì **bỏ qua bước thử khi VM tắt và bước Start VM**; không stop/start chỉ để cài monitoring.
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

**Mở sẵn trên trình duyệt:** `$SharedUrl/issues.html`, `$AzureUrl/issues.html`, Grafana `http://localhost:3000` và Route 53 Health checks. Nếu demo tự động, mở thêm Step Functions ở us-east-1. Trong Grafana chọn khoảng thời gian 15–30 phút gần nhất và refresh phù hợp với scrape 10 giây. Trước sự cố, `probe_success{job="blackbox_https"}` của cả ba URL `/health/live` phải là 1; chỉ số này đo process/HTTPS, không chứng minh database ghi được.

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

Dự kiến ban đầu các dòng status 200/environment aws. Mỗi vòng gồm thời gian request **cộng** một giây nghỉ, nên khi timeout không phải mẫu đúng mỗi giây. Probe `/version` đo tuyến và ứng dụng; RTO ghi phải lấy từ giao dịch ghi thành công ở bước 13.7. Đây không phải script tải Issues/k6 và không đo toàn bộ RPO; phép đo nâng cao làm theo mục 10.2.

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

1. Trước Stop, kiểm tra baseline, alarm OK, rule Enabled, SSM Online và đã rearm latch/incident theo mục 9.2 nếu có lượt cũ. Không bật tự động lần đầu ngay trước buổi trình bày.
2. Thực hiện Stop đúng EC2 và ghi hai mốc thời gian như chế độ A.
3. Theo dõi Step Functions và SSM theo mục 9.2, bước 5. **Không chạy lệnh promote/gate thủ công của chế độ A** trong khi controller còn hoạt động.
4. Sau execution thành công, kiểm tra Azure `/api/database/status`, `/health/database`, AWS Stopped và shared domain. Nếu workflow lỗi/timeout, kiểm tra execution/SSM command và role thực tế theo mục 9.2, bước 6 trước thao tác khác; không suy đoán promotion chưa xảy ra.

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

1. Nếu dùng tự động, làm mục 9.2, bước 6: disable EventBridge rule, xác nhận mọi execution/SSM command đã terminal. Không tiếp tục khi còn command đang chạy hoặc role chưa rõ. Không đổi flag Terraform để pause controller.
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
7. Nếu dùng tự động, chỉ rearm theo mục 9.2, bước 6 sau khi topology và commands đã được xác minh, alarm về OK. Nếu chưa định chạy lại tự động, giữ rule disabled và ghi trạng thái đó trong biên bản; không đánh dấu hệ thống sẵn sàng demo tự động.

**Kết quả hoàn thành:** AWS primary/writable, Azure standby/streaming/read-only; shared URL AWS; dữ liệu tạo trên Azure vẫn tồn tại trên cả hai bên; không có hai writer. AWS writer check Healthy, Azure writer check Unhealthy, còn ba process probes `/health/live` về 1.

### 13.10. Lưu bằng chứng và checklist trước buổi trình bày

1. Ghi `Save-DemoEvent 'demo_cycle_complete'`, dừng probe bằng Ctrl+C. Giữ `incident.csv`, `probe.csv` và các file JSON. Lưu ảnh Grafana cả giai đoạn AWS → Azure → AWS, trạng thái fencing, writer health và issue/history. Không chụp Secret/credential.
2. Nếu dùng GĐ6B, lưu execution ARN, trạng thái và mốc Step Functions/SSM. Ghi rõ chế độ thủ công hay tự động; không dùng kết quả thủ công để tuyên bố tự động đã pass.
3. Trong timeline, phân biệt thời điểm Stop yêu cầu, VM Stopped, origin được quan sát lỗi, shared Azure và ACK ghi đầu tiên. CSV `/version` không tự chứng minh RPO; muốn báo RPO phải đối chiếu giao dịch ACK/receipt theo mục 10.2. Các bước demo này chứng minh dữ liệu mẫu, không thay phép đo tải hay tối thiểu ba lượt nghiệm thu.
4. Sao chép thư mục bằng chứng khỏi thư mục tạm, export dashboard JSON và chuẩn bị video của một lượt thành công. Nếu cần dọn lab sau cùng, làm mục 12 sau khi sao lưu database; không destroy ngay trước buổi demo.

Trước buổi demo, kiểm tra:

- [ ] Hai VM chạy, đúng image/commit, AWS primary/Azure standby streaming.
- [ ] HTTPS/shared URL hoạt động, health checks đúng vai trò.
- [ ] Monitoring chạy trên laptop, đúng domain, Grafana có dữ liệu gần nhất.
- [ ] SSH hai VM dùng được; đã diễn tập rejoin/failback với release K3s pin hiện tại.
- [ ] Đã chọn một chế độ demo; controller/latch đúng trạng thái nếu dùng tự động.
- [ ] Có bản sao lưu ngoài VM, đủ dung lượng PVC mới và bằng chứng/video dự phòng.
- [ ] Laptop không sleep, mạng ổn định; không đổi cấu hình/image trong lúc trình bày.

**Nếu demo dừng giữa chừng:** ghi writer/phase thực tế, giữ bằng chứng và storage cũ. Không khởi động writer cũ để cứu màn hình. Dùng bảng lỗi ở mục 9.1 hoặc quy trình controller ở mục 9.2; chỉ tiếp tục khi fencing, role và command đang chạy đã được xác định.

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

Terraform có thể quản lý thêm tài nguyên và triển khai công cụ điều phối. Quy trình failover/failback database lúc sự cố nên do script/controller riêng xử lý với fencing và kiểm tra trạng thái; không coi một lần `terraform apply` là thao tác promote an toàn.

[Quay lại nhánh tự động](README.md) · [Lộ trình chung](../README.md).
