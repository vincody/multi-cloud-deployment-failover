# Triển khai lab AWS + Azure bằng Terraform

Thư mục này tạo lại hạ tầng của [lộ trình GĐ3–GĐ6](../../docs/giai-doan/thu_cong/README.md) bằng tài khoản, domain và image digest **của bạn**. Chạy từ máy cá nhân. Không có lệnh nào trong tài liệu này được chạy tự động khi clone repo.

## Terraform sẽ tạo gì?

| Giai đoạn | Tài nguyên được quản lý |
|---|---|
| GĐ3 AWS | VPC, public subnet, Internet Gateway, route table, security group, SSH key pair từ **public key**, EC2 Ubuntu 24.04 x86-64, EBS mã hóa, Elastic IP |
| GĐ4 Azure | Resource Group `dcs29-rg`, VNet/subnet, NSG, Standard Static Public IP, NIC, Ubuntu 24.04 x86-64 VM và disk; gắn cùng SSH public key vào tài khoản quản trị |
| GĐ5 DNS/TLS | Route 53 zone/A records/IAM; khi `enable_https=true` tự cấp certificate, TLS Secret/Ingress, redirect và renewal (**không tạo access key**) |
| GĐ6B tự động | Opt-in `enable_auto_failover`: CloudWatch/EventBridge/Step Functions/Lambda, DynamoDB latch, SSM hybrid Azure; chỉ bật sau diễn tập thủ công |
| GĐ6 failover | Hai HTTPS health check khi `enable_health_checks = true`; cặp CNAME `app` primary/secondary khi `enable_failover = true` |

VM mới cài hai cụm K3s độc lập bằng cùng `k3s_version`. Khi `enable_workload_automation=true` (mặc định), `terraform_data.workloads` dùng Python/OpenSSH từ laptop để tạo WireGuard, PostgreSQL 17/PVC/Secret, bootstrap schema trên AWS, clone standby Azure, triển khai Issues cùng GHCR digest và HTTP Ingress. Apply chỉ hoàn tất sau kiểm tra role, streaming, cùng commit và issue mẫu đã replay sang Azure. UDP 51820 chỉ mở từ IP peer; không mở 5432/6443/metrics ra Internet.

Mặc định AWS `t3.medium` (2 vCPU/4 GiB), gp3 mã hóa 30 GiB, CPU credits `standard`; Azure `Standard_B2s` (2 vCPU/4 GiB), Standard SSD OS disk 30 GiB; tăng nếu image yêu cầu lớn hơn. Đĩa local-path của lab nằm trên OS disk bền vững; automation tạo PVC 5 GiB và đặt PV reclaim policy Retain; local-path không áp quota dung lượng cứng nên vẫn cần theo dõi OS disk. Prometheus/Grafana đặt ngoài hai VM.

Terraform không đăng ký domain/đổi NS tại registrar. Khi bật `enable_https`, script tự cấp certificate, cập nhật TLS Ingress và gia hạn; Azure đọc credential DNS từ file ngoài repo, không lưu nội dung vào state. Prometheus/Grafana/k6 vẫn chạy độc lập. Mục 4 dưới đây hướng dẫn đầy đủ phần domain/HTTPS cần làm sau Terraform; [GĐ7](../../docs/giai-doan/thu_cong/GIAI_DOAN_07_OBSERVABILITY.md) và [GĐ8](../../docs/giai-doan/thu_cong/GIAI_DOAN_08_KIEM_THU_FAILOVER.md) vẫn là các bước tiếp theo. Terraform tạo **VPC riêng** trên AWS thay vì dùng default VPC của hướng dẫn Console; chức năng origin vẫn như nhau.

## 1. Chuẩn bị trên laptop

1. Cài [Terraform CLI](https://developer.hashicorp.com/terraform/install), AWS CLI và Azure CLI; xác nhận `terraform version`, `aws --version`, `az version` chạy được.
2. Có AWS account với quyền EC2, VPC, EIP, Route 53 và IAM; có Azure subscription với quyền tạo Resource Group, mạng, Public IP và VM. Kiểm tra quota, policy vùng và chi phí VM/Public IP/Route 53 trước khi apply.
3. Đăng nhập AWS bằng profile/SSO/role đang dùng trên laptop; Azure bằng `az login`. Terraform dùng các phiên đó. Chạy trong PowerShell:

   ```powershell
   aws sts get-caller-identity
   az login
   az account show --query '{subscription:id,tenant:tenantId,user:user.name}' -o table
   ```

   Nếu có nhiều AWS profile, đặt `$env:AWS_PROFILE = 'ten-profile-cua-ban'` **trước** lệnh AWS và Terraform. Nếu có nhiều Azure subscription, chạy `az account set --subscription '<subscription-id>'` rồi kiểm tra lại `az account show`. Giá trị `azure_subscription_id` ở bước 2 phải là **cùng subscription**. Không ghi access key/client secret vào `.tf` hoặc `.tfvars`.
4. Tạo **một cặp SSH key trên laptop**, bên ngoài repo, trước khi điền `terraform.tfvars`. Terraform **không tạo hoặc lưu private key**. Nếu đã có cặp key muốn dùng, bỏ qua `ssh-keygen`, đọc file `.pub` hiện có và dùng private key đi cùng file đó khi SSH ở mục 3. Với PowerShell Windows, tạo key mới bằng cách chạy từng lệnh:

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh"
   ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\dcs29_terraform"
   Get-Content "$env:USERPROFILE\.ssh\dcs29_terraform.pub"
   ```

   `ssh-keygen` hỏi passphrase: bạn có thể đặt để bảo vệ private key; khi SSH sẽ cần nhập passphrase đó. Nếu báo file đã tồn tại, **không xác nhận ghi đè** nếu bạn còn dùng key cũ. Sau khi tạo sẽ có hai file:

   | File trên laptop | Vai trò |
   |---|---|
   | `~/.ssh/dcs29_terraform.pub` | **Public key**: một dòng bắt đầu bằng `ssh-ed25519`; dán cả dòng vào biến `ssh_public_key` trong `terraform.tfvars`. |
   | `~/.ssh/dcs29_terraform` | **Private key**: giữ trên laptop; dùng với `ssh -i` sau khi tạo VM. Không dán vào Terraform, không tải lên Git/cloud. |

   Ví dụ cách điền trong `terraform.tfvars` (thay **toàn bộ** nội dung mẫu bằng dòng thật vừa in từ file `.pub`):

   ```hcl
   ssh_public_key = "ssh-ed25519 AAAA...noi-dung-public-key-cua-ban... ten-may"
   ```

   Terraform dùng public key này để tạo AWS EC2 key pair `dcs29-aws-primary-key` và cấu hình SSH cho Azure VM. **Cùng một private key trên laptop** sẽ đăng nhập được cả hai VM, với username khác nhau ở mục 3. Xác định public IPv4 hiện tại của laptop và ghi dạng `x.x.x.x/32` vào `ssh_cidr`; đây là IP được phép kết nối cổng 22, **không phải IP của VM**. Nếu IP/VPN của laptop thay đổi, cập nhật `ssh_cidr` và chạy `terraform apply` trước khi SSH lại.
5. Chọn package version ở [GĐ2](../../docs/giai-doan/thu_cong/GIAI_DOAN_02_IMAGE_VA_CI.md). `ghcr_image` là đường dẫn như `ghcr.io/vincody/multi-cloud-deployment-failover`; `image_digest` chỉ là **64 ký tự hex sau `sha256:`**, không gồm tiền tố đó. Dùng **cùng digest** cho hai VM. Không dùng tag `main`/`latest` làm bằng chứng cùng artifact.

## 2. Điền biến đầu vào

Từ **thư mục gốc repo**, mở PowerShell và chạy từng dòng:

```powershell
Set-Location deploy/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars
```

Trong `terraform.tfvars`, thay `azure_subscription_id`, `domain_name`, `ssh_public_key`, `ssh_cidr`, `image_digest`, `k3s_version` bằng giá trị thật. Chọn release K3s đã phát hành và pin đúng dạng `vX.Y.Z+k3sN`; cùng phiên bản cho hai VM. Chọn `aws_region` và `azure_region` được tài khoản cho phép; `aws_instance_type` phải là x86-64. Azure dùng `Standard_B2s` mặc định; kiểm tra quota và policy của subscription cho size này. Ví dụ trong file **không phải giá trị triển khai**: validation sẽ từ chối subscription/domain/digest/key/CIDR mẫu. Giữ cả `enable_health_checks = false` và `enable_failover = false` ở lượt apply đầu.

`terraform.tfvars`, state, plan và `.terraform/` được Git bỏ qua. **Giữ bản sao state ở nơi riêng, được bảo vệ**: mất state thì Terraform không còn biết tài nguyên nào đã tạo. Nếu nhiều người cùng chạy, thiết lập remote backend có khóa state và phân quyền trước khi chia sẻ; không commit state hoặc credential.

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

## 3. Tạo hạ tầng, kiểm tra K3s và triển khai database/app

Vẫn ở `deploy/terraform`, chạy **từng lệnh**. `terraform test` dùng provider giả, không tạo cloud resource. `plan` chỉ hiển thị thay đổi; xem kỹ account, subscription, vùng, VM size, SSH CIDR và domain trước khi apply (hai public IP thật chỉ có sau khi tạo):

```powershell
terraform init
terraform fmt -check
terraform validate
terraform test
terraform plan -out=first.tfplan
terraform apply first.tfplan
terraform output
```

Nếu apply lỗi giữa chừng, giữ nguyên state, sửa nguyên nhân và chạy `terraform plan`/`apply` lại; không xóa state để thử lại. Không apply vào tài khoản đã có các tài nguyên **cùng tên** từ lần chạy Console trước khi đã dọn hoặc import chúng.

Lấy IP và bốn nameserver từ output:

```powershell
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
terraform output -json route53_nameservers




```

Với automation bật, apply chờ cloud-init, K3s và triển khai database/app. Chế độ chỉ tạo hạ tầng có thể hoàn tất trước cloud-init. Trên từng VM SSH rồi kiểm tra:

```bash
sudo cloud-init status --wait
sudo systemctl status k3s --no-pager
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
sudo cat /opt/dcs29/README.txt
```

Để SSH, mở **PowerShell trên laptop**, đứng trong `deploy/terraform` để lấy output IP, rồi chạy **một** trong hai lệnh tùy VM muốn vào:

```powershell
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
ssh -i "$env:USERPROFILE\.ssh\dcs29_terraform" "ubuntu@$AwsIp"
ssh -i "$env:USERPROFILE\.ssh\dcs29_terraform" "azureuser@$AzureIp"
```

AWS dùng username `ubuntu`; Azure dùng `azureuser` mặc định (nếu đã đổi `azure_admin_username`, thay `azureuser` trong lệnh). `-i` trỏ tới **private key không có `.pub`**; nếu dùng key có sẵn, thay đường dẫn ví dụ bằng đường dẫn private key tương ứng. Hai lệnh SSH là hai phiên riêng; thoát phiên thứ nhất bằng `exit` rồi mới vào phiên thứ hai. Sau khi vào VM, chạy block Bash kiểm tra cloud-init ở trên. Nếu không kết nối được, kiểm tra đúng private key, đúng username/IP và `ssh_cidr` có phải public IP hiện tại của laptop hay không.

Nếu cloud-init lỗi, xem `sudo tail -n 200 /var/log/cloud-init-output.log` và `sudo journalctl -u k3s --no-pager -n 100`. Xác nhận release K3s tồn tại và máy truy cập được repository cài đặt.

### 3.1. PostgreSQL và Issues

Phần database/app đã do Terraform automation thực hiện. Chuẩn bị thêm Python 3.10+, OpenSSH và các biến sau trước plan:

```hcl
enable_workload_automation = true
ssh_private_key_path = "C:/Users/YOUR_USER/.ssh/dcs29_terraform"
postgres_image = "docker.io/library/postgres@sha256:DIGEST_THAT_CUA_POSTGRES_17_BOOKWORM"
automation_python = "python"
standby_storage_generation = "initial"
```

Lấy digest bằng `docker buildx imagetools inspect postgres:17-bookworm`; kiểm tra linux/amd64, Debian và UID postgres 999. Các placeholder phải được thay thật. Key có passphrase phải nạp vào SSH agent trước vì automation không hỏi mật khẩu.

Trong SSH từng VM:

```bash
sudo wg show wg0
sudo k3s kubectl -n issues get pods,pvc,svc,endpointslices
sudo cat /opt/dcs29/automation-result.json
```

Trên laptop đọc `http://AWS_IP/api/database/status` và `http://AZURE_IP/api/database/status`: AWS primary/writable, Azure standby/streaming/read-only. `/health/database` chỉ 200 ở writer; standby 503 là đúng.

Password do VM sinh và giữ root-only, chuyển tới Azure qua SSH; không đưa vào Terraform state/user_data. Script giữ credential/PVC/request ID khi chạy lại, từ chối database thủ công hoặc role đã đổi sau failover. Không tự migrate dữ liệu Compose cũ hoặc nâng major PostgreSQL.

**Sau reboot K3s bị guard chặn**; chỉ xác nhận marker và start sau khi biết writer hiện tại, không bật AWS cũ khi Azure đã promote. Hướng dẫn chi tiết về retry, guard, HTTPS và vận hành được trình bày đầy đủ tại [nhánh tu_dong](../../docs/giai-doan/tu_dong/TRIEN_KHAI_BANG_TERRAFORM.md) và [chi tiết automation](../../docs/giai-doan/tu_dong/AUTOMATION_WIREGUARD_POSTGRESQL_APP.md).

Giữ automation bật sau triển khai để không gỡ rule WireGuard. Khi chỉ muốn tạo hạ tầng ngay từ đầu, đặt `enable_workload_automation=false`, `postgres_image=""`, `ssh_private_key_path=""`; database/app chưa được triển khai.

### 3.2. Nếu VM đã tồn tại từ bản Docker Compose

Terraform giữ `ignore_changes` cho AWS AMI/user_data và Azure custom_data nên **không tự chuyển VM cũ sang K3s** khi sửa tfvars. Resize VM có thể ngắt dịch vụ; xem plan và sao lưu trước. Không giảm disk, xóa volume hoặc chạy uninstall K3s để thử lại.

Với VM cũ, lưu cấu hình Compose/TLS và sao lưu database, rồi làm hướng dẫn cài K3s GĐ3/GĐ4 bằng cùng phiên bản pin. Kiểm tra app qua port-forward trước, sau đó dừng NGINX cũ để giải phóng 80/443 và chuyển Ingress. Giữ volume cũ để rollback; không chạy đồng thời hai primary. Không chạy lại toàn bộ user_data trên VM đã có K3s vì có thể ghi đè cấu hình hiện tại.

## 4. Tự động domain certificate, Ingress HTTPS và renewal

HTTPS là lượt apply riêng sau khi domain đã delegate. Terraform tạo `terraform_data.https`, dùng SSH để cài Certbot/DNS-01, cấp certificate, tạo TLS Secret `issues-tls`, cập nhật Ingress và lịch gia hạn. User **không cần SSH cấu hình Certbot hoặc tự dán YAML TLS**.

### 4.1. Đổi nameserver tại nhà đăng ký domain

Sau lượt apply HTTP, từ **PowerShell laptop**, thư mục `deploy/terraform`:

```powershell
terraform output -json route53_nameservers
$Domain = 'YOUR_DOMAIN'
$AwsIp = terraform output -raw aws_public_ip
$AzureIp = terraform output -raw azure_public_ip
```

Thay YOUR_DOMAIN bằng `domain_name` thật. Trong trang domain của **nhà đăng ký**, chọn Nameservers → Custom và thay bằng đủ bốn nameserver Terraform xuất ra. Không tạo hosted zone khác hoặc tạo lại hai A record origin. Nếu domain có website/email, sao chép các record cần giữ vào zone mới trước đổi NS; xử lý DS/DNSSEC cũ tại nhà đăng ký nếu có.

Kiểm tra DNS bằng **PowerShell laptop**:

```powershell
Resolve-DnsName $Domain -Type NS -Server 1.1.1.1
Resolve-DnsName $Domain -Type NS -Server 8.8.8.8
Resolve-DnsName "aws-origin.$Domain" -Type A -Server 1.1.1.1
Resolve-DnsName "azure-origin.$Domain" -Type A -Server 1.1.1.1
```

NS phải đúng bốn nameserver; A record phải đúng IP VM. Automation cũng kiểm tra NS/A qua cả hai resolver, dừng trước cấp certificate nếu DNS chưa sẵn sàng. Nếu domain có CAA hạn chế nhà cấp, cho phép Let's Encrypt. Record `app` chưa cần tồn tại vì cấp bằng DNS-01; TLS certificate vẫn chứa hostname chung.

### 4.2. Chuẩn bị quyền DNS cho Azure, không ghi credential vào state

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

Nếu Azure đã có credential root được cấu hình trước đó, có thể để `azure_dns_credentials_file=""` để dùng lại; vẫn phải đảm bảo đúng profile `[default]` và quyền DNS. Đổi nội dung file cùng đường dẫn không tạo trigger Terraform; khi rotate key, yêu cầu chạy lại HTTPS resource ở mục 4.5.

### 4.3. Bật HTTPS và apply

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

### 4.4. Kiểm chứng HTTPS và gia hạn

Trên **PowerShell laptop**:

```powershell
curl.exe --fail-with-body "https://aws-origin.$Domain/version"
curl.exe --fail-with-body "https://azure-origin.$Domain/version"
curl.exe --fail-with-body --resolve "app.${Domain}:443:${AwsIp}" "https://app.$Domain/version"
curl.exe --fail-with-body --resolve "app.${Domain}:443:${AzureIp}" "https://app.$Domain/version"
curl.exe -I "http://aws-origin.$Domain/version"
curl.exe -I "http://azure-origin.$Domain/version"
```

Hai origin đúng environment/cùng commit. Hostname chung ép đến từng IP phải qua kiểm tra TLS thật; không `-k`. HTTP phải redirect HTTPS. Chỉ khi đã publish failover DNS ở mục 5 mới kiểm tra `app` bằng DNS thật.

Automation cũng kiểm tra `/version` bằng HTTPS trên từng VM với SNI/hostname, public trust chain và redirect. Kết quả ghi `/opt/dcs29/https-result.json`. Nó không thay kiểm tra truy cập Internet từ laptop.

Renewal tự chạy qua `certbot.timer`: AWS 00:00/12:00 UTC, Azure 06:00/18:00 UTC, không random delay hoặc catch-up lúc boot. Service giới hạn 45 phút để giữ các lượt bình thường cách xa nhau; deploy hook cập nhật `issues-tls` khi gia hạn thành công. Lịch lệch giờ giảm xung đột TXT; **không phải distributed lock**. Không chạy renewal/cấp lại thủ công đồng thời hai VM, kiểm tra đồng bộ giờ hệ thống và theo dõi lỗi renewal.

Trong **SSH từng VM**, các lệnh sau chỉ dành kiểm chứng/debug, không phải cấu hình bắt buộc:

```bash
sudo cat /opt/dcs29/https-result.json
sudo certbot certificates
systemctl list-timers --all certbot.timer
sudo systemctl status certbot.timer --no-pager
sudo journalctl -u certbot.service --no-pager -n 100
sudo k3s kubectl -n issues get ingress,middleware
```

Nếu muốn nghiệm thu ACME renewal, chạy dry-run **lần lượt**, AWS xong mới đến Azure; thao tác này gọi staging ACME, không thay production certificate:

```bash
sudo certbot renew --cert-name dcs29-aws --dry-run
```

Trên Azure dùng lệnh riêng với credential đã cài:

```bash
sudo env AWS_PROFILE=default AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials certbot renew --cert-name dcs29-azure --dry-run
```

VM tắt thì không gia hạn. Khi bật sau thời gian dài, xác nhận writer/guard trước khi start K3s, kiểm tra hạn certificate rồi chạy lại HTTPS automation nếu cần.

### 4.5. Retry, credential rotation và phạm vi

Nếu DNS/CAA/credential hoặc Ingress lỗi, sửa đúng nguyên nhân và giữ state/certificate/Secrets. Không force-renew liên tục để tránh rate limit. Script cố bật lại lịch renewal cho cả hai VM kể cả khi một phase thất bại.

Từ **PowerShell laptop**, sau khi sửa hoặc rotate credential:

```powershell
terraform plan -replace='terraform_data.https[0]' -out=https-retry.tfplan
terraform apply https-retry.tfplan
```

Đọc plan để bảo đảm không replace VM/disk. `enable_https=false` chỉ bỏ orchestration khỏi Terraform, **không tự gỡ certificate, Ingress hoặc timer đã cài**; không dùng biến này như nút tắt HTTPS. Chưa có cơ chế tự rollback TLS sau mọi lỗi.

Không tự đăng ký domain/đổi NS tại registrar, tạo secret access key, promote database, gỡ startup guard hoặc bật DNS failover. Sau HTTPS, chuyển sang mục 5 để bật health check rồi publish failover theo hai lượt. Health check phụ thuộc HTTPS resource khi bật để không tạo trước khi TLS automation hoàn tất.

## 5. Bật failover (GĐ6)

Sau khi GĐ5 pass, sửa `enable_health_checks = true` trong `terraform.tfvars` (giữ `enable_failover = false`), rồi chạy:

```powershell
terraform plan -out=checks.tfplan
terraform apply checks.tfplan
terraform output health_check_ids
```

Plan lần này chỉ nên thêm **hai health check HTTPS**, chưa có record `app`. Mở Route 53, xác nhận endpoint mỗi check là origin riêng, port 443, path `/health/database`, SNI bật; **AWS primary Healthy, Azure standby Unhealthy là trạng thái bình thường**. Azure chỉ Healthy sau fencing AWS và promote PostgreSQL. Kiểm tra TLS/read API Azure riêng trước khi publish domain chung.

Sau đó sửa `enable_failover = true` trong `terraform.tfvars` và chạy:

```powershell
terraform plan -out=failover.tfplan
terraform apply failover.tfplan
```

Plan lần này chỉ nên thêm **hai CNAME failover `app`**. Kiểm tra DNS authoritative, `https://app.<domain>/version`, thử AWS → Azure → AWS theo [GĐ6](../../docs/giai-doan/thu_cong/GIAI_DOAN_06_ROUTE53_FAILOVER.md). **Không tạo lại health check hoặc record `app` thủ công** khi Terraform đã quản lý chúng.

## 6. Failover tự động GĐ6B (lượt apply thứ năm)

Sau diễn tập fencing/promote/rejoin/failback thủ công, trở về AWS primary/Azure standby streaming rồi giữ bốn flag workload/HTTPS/health check/DNS bật và thêm `enable_auto_failover=true`. AWS CLI phải cùng account Terraform, có quyền SSM hybrid activation/registration/Parameter Store và PassRole cho `dcs29-azure-ssm`.

```powershell
terraform init
terraform validate
terraform test
terraform plan -out=auto-failover.tfplan
terraform apply auto-failover.tfplan
terraform output auto_failover
```

Đọc plan trước apply; không replace VM/disk/PVC. Bootstrap đăng ký SSM Azure và kiểm tra Online; controller ở us-east-1 tự xác minh sự cố, chờ EC2 stopped rồi mới promote Azure. Nếu API EC2 không xác nhận được fencing, workflow dừng. Route 53 chọn writer sau readiness; async replication vẫn có thể mất WAL chưa truyền. Failback/rejoin không tự chạy.

Các bước quyền, Console verification, demo, tắt trigger khi bảo trì, kiểm tra command và rearm được ghi đầy đủ ngay ở **mục 9.2** của [hướng dẫn Terraform](../../docs/giai-doan/tu_dong/TRIEN_KHAI_BANG_TERRAFORM.md). `enable_auto_failover=false` gỡ control resources, không phải nút pause; dùng EventBridge disable-rule khi bảo trì. Chưa nghiệm thu workflow trên cloud.

## Cập nhật image, dọn lab và lưu state

- Với automation bật và AWS primary/Azure standby, đổi `image_digest` rồi plan/apply sẽ triển khai app mới trên cả hai cụm và kiểm chứng dữ liệu/commit; không tạo lại VM. Không dùng initial automation khi writer đã đổi sau failover. Nâng K3s hoặc migration schema cần quy trình riêng và sao lưu trước; chỉnh `k3s_version` không tự nâng VM đã tồn tại.
- DNS không fence/promote database. Thực hiện [GĐ6](../../docs/giai-doan/thu_cong/GIAI_DOAN_06_ROUTE53_FAILOVER.md) đúng thứ tự: chặn ghi/drain, đợi WAL nếu planned, fence, promote, rồi kiểm tra writer health. Route 53 có thể trả endpoint khi cả hai unhealthy; không xem DNS là khóa bảo vệ một writer.
- Nếu bật GĐ6B, disable rule và xác nhận workflow/SSM commands đã terminal trước maintenance/destroy; deregister đúng Azure hybrid node và xóa `/dcs29/dr/azure-node` theo mục 9.2 của hướng dẫn đầy đủ. Không Start writer AWS cũ.
- Trước destroy sao lưu dữ liệu PostgreSQL ra ngoài VM: PVC local-path trên OS disk không tồn tại sau xóa VM/disk.
- Trước khi dọn, lưu kết quả GĐ8, xóa **access key tạo thủ công** của IAM user `dcs29-azure-certbot`; nếu còn key, AWS sẽ từ chối xóa user do Terraform quản lý. Đưa nameserver tại registrar về nơi bạn muốn dùng sau lab. Sau đó xem `terraform plan -destroy` và chạy `terraform destroy` trong đúng workspace/state. Terraform chỉ xóa tài nguyên trong state, không xóa domain đã đăng ký hoặc image GHCR. Nếu zone có record do bạn tạo thủ công ngoài Terraform, xử lý chúng trước khi destroy.
- Stop VM không tương đương `destroy`: disk, static/public IP, Route 53 hosted zone và health checks vẫn có thể phát sinh phí. Giữ state an toàn cho lần `apply`/`destroy` tiếp theo.

## Nguồn cấu hình

- [K3s: cài đặt và cấu hình phiên bản](https://docs.k3s.io/installation/configuration).
- [Azure Bv1/B2s: thông số và vòng đời](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bv1-series): kiểm tra lịch ngừng cung cấp, quota và khả dụng trong vùng trước apply.
