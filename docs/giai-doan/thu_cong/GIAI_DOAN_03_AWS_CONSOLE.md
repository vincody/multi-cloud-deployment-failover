# GĐ3 — Hướng dẫn thao tác AWS Console và triển khai EC2

**Kết quả cần đạt:** một origin AWS độc lập chạy K3s cho Issues/PostgreSQL qua Elastic IP, chạy đúng `GHCR_IMAGE@sha256:IMAGE_DIGEST` lấy ở GĐ2. Tài liệu này bao gồm VM/K3s, PostgreSQL primary, schema và app AWS; Azure standby triển khai trong hướng dẫn Azure Portal, domain/HTTPS ở GĐ5. Cloud chưa nghiệm thu theo kiến trúc mới. Điền [bảng thông số](THONG_SO_TRIEN_KHAI.md) trước khi nhập các ký hiệu mẫu vào lệnh.

**Triển khai hai lượt để không bị kẹt ở WireGuard:** lượt 1 làm AWS mục 1–9 tới namespace/startup guard, rồi tạo Azure VM/K3s/namespace/guard ở Portal mục 1–7. Sau khi có IP/key hai phía, lượt 2 cấu hình WireGuard cả hai, hoàn tất primary/schema/app AWS rồi mới apply clone/standby/app Azure. Không chạy toàn bộ Azure trước khi primary/schema AWS sẵn sàng.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Dựng EC2, K3s, PostgreSQL primary và Issues AWS. |
| Chuẩn bị | Có AWS account, image digest; chuẩn bị cả hai VM trước khi cấu hình WireGuard. |
| Hoàn tất khi | AWS phục vụ Issues; database primary và schema sẵn sàng. |

**Luồng thao tác:** [2. Chọn region và tạo Budget](#2-chọn-region-và-tạo-budget) → [4. Tạo Security Group](#4-tạo-security-group) → [5. Tạo key pair và EC2](#5-tạo-key-pair-và-ec2) → [8. Cài K3s — từng bước trong SSH](#8-cài-k3s--từng-bước-trong-ssh) → [9. Chuẩn bị namespace và image](#9-chuẩn-bị-namespace-và-image) → [9A. Cấu hình WireGuard và PostgreSQL thực tế](#9a-cấu-hình-wireguard-và-postgresql-thực-tế) → [10. Kiểm tra app AWS sau khi hoàn tất phần database ở trên](#10-kiểm-tra-app-aws-sau-khi-hoàn-tất-phần-database-ở-trên) → [13. Checklist](#13-checklist).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Cần tạo những gì?

```text
Internet → Elastic IP → Security Group (80) → EC2 Ubuntu
                                        └─ K3s: Traefik → Service → Issues → PostgreSQL primary
```

| Tài nguyên                  | Số lượng | Giá trị gợi ý                                                    |
| ----------------------------- | ----------: | -------------------------------------------------------------------- |
| Default VPC và public subnet | Dùng sẵn | Region `AWS_REGION` bạn chọn theo quota/chi phí |
| Security Group                |           1 | SSH 22 chỉ từ IP quản trị; HTTP 80 từ Internet; không mở 8080 |
| EC2                           |           1 | Ubuntu 24.04 LTS x86_64, **t3.medium**, 2 vCPU / 4 GiB        |
| EBS root                      |           1 | `gp3`, **30 GiB ban đầu**                                              |
| Elastic IP                    |           1 | Gắn EC2 để stop/start không đổi IP origin                      |
| Key pair                      |           1 | RSA`.pem` để SSH; giữ ngoài repository                         |
| AWS Budget                    |           1 | Cảnh báo chi phí theo hạn mức nhóm chọn                       |

Image app bắt buộc: `GHCR_IMAGE@sha256:IMAGE_DIGEST` từ **package version đã chọn ở GĐ2** cho `linux/amd64`, commit `COMMIT_SHA`; GĐ4 Azure sẽ dùng **cùng digest**. Package public có thể pull thẳng từ GHCR, không cần token. [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md).

Trước khi làm, chuẩn bị AWS account có quyền EC2/VPC/EIP/Billing, IP công khai của laptop để giới hạn SSH, và GitHub token **classic** có `read:packages` nếu package GHCR còn private. GitHub account của token phải được cấp quyền đọc package. Không đưa token, `.pem` hay secret vào repo hoặc screenshot. [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Ghi lại các ID sau trong sổ triển khai: `vpc-id`, `subnet-id`, `sg-id`, `instance-id`, `ami-id`, `volume-id`, `eipalloc-id`, địa chỉ Elastic IP, instance type, thời gian UTC. Không ghi credential.

## 2. Chọn region và tạo Budget

1. Mở AWS Console, chọn region đã ghi là **`AWS_REGION`** ở góc trên bên phải (ví dụ Singapore có mã `ap-southeast-1`). Kiểm tra lại region khi chuyển giữa VPC và EC2 Console.
2. Vào **Billing and Cost Management → Budgets → Create budget**. Chọn **Monthly cost budget** hoặc **Customize → Cost budget**. Nhập ngân sách tháng theo mức nhóm chấp nhận, email và ngưỡng cảnh báo, rồi tạo. Budget **chỉ cảnh báo**, không tự giới hạn hóa đơn. [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/create-cost-budget.html).
3. Kiểm tra giá ở [AWS Pricing Calculator](https://calculator.aws/) cho instance type, EBS, public IPv4 và thời gian dự kiến chạy trong region này. Không mặc định instance được Free Tier. Elastic IP/public IPv4 vẫn tính phí kể cả đã gắn với EC2. [AWS Elastic IP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/working-with-eips.html).

## 3. Xác nhận mạng public

1. **VPC Console → Your VPCs**: Tìm dòng có **Default VPC = Yes**. Sao chép mã ở cột **VPC ID** (ví dụ: `vpc-0123456789abcdef0`) và lưu vào sổ triển khai để dùng ở các bước tiếp theo.
2. **VPC Console → Subnets**: Lọc theo mã **VPC ID** vừa sao chép, chọn dòng có **Default Subnet = Yes**. Sao chép mã ở cột **Subnet ID** và tên **Availability Zone** vào sổ triển khai.
3. **VPC Console → Route Tables**: xác nhận route table của subnet có `0.0.0.0/0` tới **Internet Gateway** `igw-...`. Nếu không có, không dùng subnet này làm public origin.
4. Nếu region chưa có default VPC và account cho phép, dùng **Your VPCs → Actions → Create Default VPC** rồi kiểm tra lại subnet/route. Không tạo NAT Gateway cho GĐ3. [AWS default VPC](https://docs.aws.amazon.com/en_en/vpc/latest/userguide/work-with-default-vpc.html).

Nếu đây là account dùng chung do trường/nhóm quản lý, xác nhận mạng được phép dùng trước khi tạo VPC mới.

## 4. Tạo Security Group

Vào **EC2 Console → Network & Security → Security Groups → Create security group**:

| Trường            | Giá trị                                                        |
| ------------------- | ---------------------------------------------------------------- |
| Security group name | `dcs29-aws-primary-sg`                                         |
| Description         | `DCS29 AWS HTTP origin`                                        |
| VPC                 | `vpc-id` ở bước 3                                           |
| Inbound 1           | `SSH`, TCP 22, source **My IP** (`/32` của laptop)    |
| Inbound 2           | `HTTP`, TCP 80, source **Anywhere-IPv4** (`0.0.0.0/0`) |
| Outbound            | Giữ mặc định cho apt, Docker và GHCR                        |

Không mở 8080, DB 5432, API 6443 hoặc NodePort ra toàn Internet. Rule tunnel chỉ cho peer Azure sau khi chốt mạng. Chưa mở 443 ở GĐ3; đến GĐ5 mới thêm. Thêm tags `Project=dcs29`, `Owner=<tên nhóm>`, tạo SG và ghi `sg-id`. Vào lại **Inbound rules** để bảo đảm Console không để SSH mở cho `0.0.0.0/0`. [AWS Security Groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/changing-security-group.html).

## 5. Tạo key pair và EC2

### Key pair

**EC2 → Network & Security → Key Pairs → Create key pair**: name `dcs29-aws-primary-key`, type `RSA`, file format `.pem`. Tải và giữ file `.pem` ngoài repository. Nếu đã có key pair phù hợp và còn private key, dùng lại.

### Launch instance

**EC2 → Instances → Launch instances**, kiểm tra từng mục trước khi bấm **Launch instance**:

| Mục                       | Giá trị GĐ3                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------- |
| Name                       | `dcs29-aws-primary`                                                                 |
| AMI                        | Ubuntu Server**24.04 LTS**, Canonical, **64-bit (x86)**; ghi AMI ID thật |
| Instance type              | **t3.medium**, cấu hình đã chốt                              |
| Key pair                   | Key ở trên                                                                          |
| Network settings → VPC    | Default VPC đã kiểm tra                                                            |
| Network settings → Subnet | Public default subnet đã kiểm tra                                                  |
| Auto-assign public IP      | **Disable**; bước 6 sẽ gắn Elastic IP                                       |
| Firewall                   | **Select existing security group** → `dcs29-aws-primary-sg`                  |
| Configure storage          | Root EBS`gp3`, **30 GiB ban đầu**                                                       |
| Number of instances        | 1                                                                                     |

Thêm tags `Project=dcs29`, `Owner=<tên nhóm>` cho instance và volume khi Console cho chọn resource types. Xem **Summary** và ước tính chi phí, rồi launch. Chờ trạng thái **Running** và **status checks passed** theo Console. Ghi `instance-id`, `ami-id`, `volume-id`. Image GĐ2 là `linux/amd64` nên không chọn instance Graviton/Arm. [AWS Launch instance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html).

**CPU credit:** T3 mặc định Unlimited; ghi Standard/Unlimited đã chọn. Standard giới hạn khi hết credit; Unlimited có thể phát sinh phí surplus. [AWS credit](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode-concepts.html).

## 6. Cấp và gắn Elastic IP

1. **EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address**. Dùng pool mặc định của Amazon, network border group thông thường của region, thêm tags rồi **Allocate**.
2. Chọn IP vừa cấp → **Actions → Associate Elastic IP address** → Resource type **Instance** → chọn `instance-id` → **Associate**.
3. Ghi IP và `eipalloc-id`. Ở **Instances → Networking**, xác nhận public IPv4 của instance chính là IP này.

Elastic IP phải cùng network border group với EC2 và được tính phí khi còn allocate. [AWS allocate/associate EIP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/working-with-eips.html).

## 7. SSH và kiểm tra EC2

Laptop Windows PowerShell, thay đường dẫn key và Elastic IP:

```powershell
ssh -i "C:\duong-dan-ngoai-repo\dcs29-aws-primary-key.pem" ubuntu@AWS_PUBLIC_IP
```

Nếu timeout kiểm tra Running, EIP, route IGW, SG 22 và IP /32 laptop. Nếu xác thực lỗi kiểm tra user ubuntu/key pair. Các lệnh tiếp theo chạy trong SSH Ubuntu:

```bash
uname -m
nproc
free -h
df -h /
```

Kỳ vọng x86_64, 2 vCPU, RAM gần 4 GiB, disk 30 GiB ban đầu.

## 8. Cài K3s — từng bước trong SSH

K3s có containerd tích hợp; không cài Docker/Compose cho workload này. Hai VM là hai server single-node độc lập.

**Bước 1 — chuẩn bị:**

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
```

**Bước 2 — chọn version:** chọn release phù hợp từ [K3s releases](https://github.com/k3s-io/k3s/releases), ghi K3S_VERSION đầy đủ vào sổ triển khai. Cùng version trên Azure. Thay literal K3S_VERSION trước khi chạy:

```bash
curl -sfL https://get.k3s.io -o /tmp/install-k3s.sh
sudo env INSTALL_K3S_VERSION='K3S_VERSION' sh /tmp/install-k3s.sh
```

Đợi shell trả dấu nhắc. Không dùng K3S_URL/K3S_TOKEN để join cloud kia; token/kubeconfig không commit.

**Bước 3 — kiểm tra:**

```bash
sudo systemctl is-active k3s
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
```

Service active, node Ready, system pods ổn định. Nếu lỗi:

```bash
sudo journalctl -u k3s -n 100 --no-pager
sudo k3s kubectl get events -A --sort-by=.metadata.creationTimestamp
```

Traefik/ServiceLB dùng cổng ingress; không chạy NGINX Compose cũ tranh 80/443. [K3s configuration](https://docs.k3s.io/installation/configuration), [networking](https://docs.k3s.io/networking/networking-services).

## 9. Chuẩn bị namespace và image

```bash
sudo mkdir -p /opt/dcs29
sudo chown "$USER:$USER" /opt/dcs29
cd /opt/dcs29
sudo k3s kubectl create namespace issues --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Ghi GHCR_IMAGE@sha256:IMAGE_DIGEST của bản Issues từ GĐ2; không dùng digest dashboard cũ. Tiếp tục các mục 9.1–9.7 ngay bên dưới để chuẩn bị mạng, PostgreSQL primary, schema và app. Namespace có sẵn chưa đủ để chạy API Issues.

Public package K3s tự pull khi tạo pod; private package cần imagePullSecret. Docker login trên host không tự cấp credential cho K3s/containerd.


### Chặn K3s tự khởi động workload sau reboot — làm cả hai VM

Để primary cũ không tự ghi khi VM phục hồi sau failover, demo này yêu cầu phê duyệt khởi động K3s **mỗi lần boot VM**. File phê duyệt nằm trên /run, mất khi reboot. Khi chỉ restart pod/service trong cùng boot, file vẫn còn.

Trên SSH mỗi VM, sau khi K3s cài xong:

```bash
sudo install -d /etc/systemd/system/k3s.service.d
sudo tee /etc/systemd/system/k3s.service.d/10-dr-start-guard.conf >/dev/null <<'EOF'
[Service]
ExecStartPre=/usr/bin/test -f /run/issues-k3s-start-approved
EOF
sudo touch /run/issues-k3s-start-approved
sudo systemctl daemon-reload
```

Mẫu không stop dịch vụ đang chạy. **Sau reboot bình thường:** kiểm tra bên kia đang primary hay standby bằng API/database và ghi writer thực tế. Chỉ khi chắc chắn VM này chưa trở thành primary cũ bị fence, chạy:

```bash
sudo touch /run/issues-k3s-start-approved
sudo systemctl reset-failed k3s
sudo systemctl start k3s
sudo k3s kubectl get nodes,pods -A
```

**Sau failover:** không tạo file phê duyệt ngay. Theo runbook GĐ6 để đổi storage/config primary cũ trước khi start K3s. Node Ready sau reboot là bước cần kiểm tra thủ công, không tự bật hai writer. WireGuard vẫn có thể lên để SSH/clone khi K3s bị chặn. Kiểm tra override bằng `sudo systemctl cat k3s`; đây là guard vận hành demo, không thay thế fencing bằng Stop VM đã xác nhận.

## 9A. Cấu hình WireGuard và PostgreSQL thực tế


### Chuẩn bị WireGuard — làm trên mỗi VM

Chọn dải tunnel `10.200.29.0/24` nếu không trùng VPC/VNet, mạng laptop hoặc K3s. AWS là `10.200.29.1`, Azure là `10.200.29.2`. Chỉ định tuyến tới IP peer `/32`, không chuyển toàn bộ Internet hoặc ghép hai cluster. PostgreSQL dùng hostNetwork và bind tunnel IP, nên không cần quảng bá pod CIDR xuyên cloud.

Trước cấu hình, hai VM phải có public IP tĩnh và K3s Ready. Trên SSH mỗi VM:

```bash
sudo apt-get update
sudo apt-get install -y wireguard openssl gettext-base
sudo install -d -m 700 /etc/wireguard
sudo bash -c 'set -euo pipefail; umask 077; test ! -e /etc/wireguard/private.key; wg genkey > /etc/wireguard/private.key; wg pubkey < /etc/wireguard/private.key > /etc/wireguard/public.key'
sudo cat /etc/wireguard/public.key
```

Ghi public key của mỗi bên để nhập ở peer bên kia. Nếu private.key đã có, không tạo lại; dùng public.key hiện có. Private key chỉ ở VM tương ứng, không copy/commit/chụp ảnh.

Firewall cloud: AWS SG thêm **Custom UDP 51820, source AZURE_PUBLIC_IP/32**; Azure NSG thêm inbound **UDP 51820, source AWS_PUBLIC_IP/32**, priority chưa dùng (ví dụ 330; dành 310 cho HTTP, 320 cho HTTPS). Chỉ public IP peer được phép vào port tunnel. Không thêm public TCP 5432. Kiểm tra outbound cho phép UDP tới peer.

Nếu host bật UFW, kiểm tra `sudo ufw status verbose`; giữ rule SSH `/32` và rule K3s nội bộ trước khi thay đổi. Thêm rule UDP nguồn peer và TCP 5432 trên wg0 nguồn tunnel peer. Trên **AWS**:

```bash
sudo ufw allow from AZURE_PUBLIC_IP to any port 51820 proto udp
sudo ufw allow in on wg0 from 10.200.29.2 to 10.200.29.1 port 5432 proto tcp
```

Trên **Azure**, dùng source/IP ngược lại:

```bash
sudo ufw allow from AWS_PUBLIC_IP to any port 51820 proto udp
sudo ufw allow in on wg0 from 10.200.29.1 to 10.200.29.2 port 5432 proto tcp
```


Không tự bật UFW nếu đang inactive chỉ để chạy demo; SG/NSG vẫn giới hạn Internet. Mẫu này không cần IP forwarding giữa hai tunnel peer vì DB lắng nghe trực tiếp IP wg0.

### Cấu hình wg0 — SSH AWS

Thay public IP và public key peer thật trước khi chạy. Private key được đọc vào file trong VM, không in ra terminal:

```bash
sudo bash -c 'set -euo pipefail; umask 077; test ! -e /etc/wireguard/wg0.conf; cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.200.29.1/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/private.key)

[Peer]
PublicKey = AZURE_WIREGUARD_PUBLIC_KEY
Endpoint = AZURE_PUBLIC_IP:51820
AllowedIPs = 10.200.29.2/32
PersistentKeepalive = 25
EOF'
sudo systemctl enable --now wg-quick@wg0
sudo wg show wg0
ip route get 10.200.29.2
ping -c 3 10.200.29.2
```

Cấu hình peer bên kia xong mới có handshake. Nếu ping bị firewall chặn, kiểm tra handshake/transfer và kết nối PostgreSQL sau; chưa có handshake thì kiểm tra key, IP endpoint, UDP SG/NSG và UFW. File đã tồn tại thì dùng `sudo nano /etc/wireguard/wg0.conf`, sau đó `sudo systemctl restart wg-quick@wg0`; không ghi đè key mới.

`sudo wg show` che private key; không dùng `wg showconf` khi ghi evidence. Mỗi VM reboot phải tự lên wg0. Nếu đổi public IP, cập nhật Endpoint bên kia. Nguồn: [WireGuard quick start](https://www.wireguard.com/quickstart/).

### Credential PostgreSQL và image — trước khi apply

Chọn cùng digest PostgreSQL **17-bookworm, linux/amd64** ở hai bên; không dùng PostgreSQL 18 vì cấu trúc image/PGDATA khác. Với Docker trên laptop có thể pull `postgres:17-bookworm` rồi lấy RepoDigests:

```powershell
docker pull --platform linux/amd64 postgres:17-bookworm
docker image inspect postgres:17-bookworm --format '{{index .RepoDigests 0}}'
```

Ghi giá trị `postgres@sha256:...` đã chọn. Tag chỉ dùng để chọn release; manifest thật dùng digest. Mẫu dùng user postgres UID/GID 999 của image bookworm; nếu đổi image, kiểm tra UID/GID trước khi dùng init container chown.

**Chỉ AWS, tạo credential một lần** trên SSH:

```bash
cd /opt/dcs29
test ! -e postgres.env || { echo 'postgres.env đã tồn tại: dùng credential hiện có, không tạo lại'; exit 1; }
umask 077
printf 'POSTGRES_PASSWORD=%s\nAPP_DB_PASSWORD=%s\nREPLICATION_PASSWORD=%s\n' "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > postgres.env
sudo k3s kubectl -n issues create secret generic postgres-auth --from-env-file=postgres.env --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Hex password tránh ký tự đặc biệt cần URL-encode trong DSN. Lưu file ngoài Git với quyền 600. **Azure cần cùng file postgres.env** vì physical clone sao chép tài khoản/password DB. Copy bằng SCP qua laptop trong thư mục riêng, sau khi SSH key đã xác nhận đúng VM; không truyền file qua Git/chat. PowerShell laptop, thay các key/IP:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE/.dcs29-private"
scp -i PATH_TO_AWS_KEY ubuntu@AWS_PUBLIC_IP:/opt/dcs29/postgres.env "$env:USERPROFILE/.dcs29-private/postgres.env"
scp -i PATH_TO_AZURE_KEY "$env:USERPROFILE/.dcs29-private/postgres.env" AZURE_ADMIN_USER@AZURE_PUBLIC_IP:/opt/dcs29/postgres.env
```

Trên **Azure**, không tạo password mới:

```bash
cd /opt/dcs29
chmod 600 postgres.env
sudo k3s kubectl -n issues create secret generic postgres-auth --from-env-file=postgres.env --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Đổi Secret không tự đổi password trong PostgreSQL đã init. Đây là lần cài fresh; với dữ liệu cũ cần thao tác ALTER ROLE có kiểm soát, không re-init/xóa PVC.

### PostgreSQL config chung — tạo file trên AWS

Trên SSH, mở `nano /opt/dcs29/postgres-config.yaml`, dán YAML sau. Mỗi cloud có ConfigMap riêng; listen_addresses phải đúng tunnel IP máy đó. Mẫu cấp quyền app từ pod CIDR K3s mặc định `10.42.0.0/16`; nếu đã đổi cluster-cidr hãy đổi pg_hba cho đúng. DB dùng hostNetwork, chỉ bind localhost và wg0, không bind public/private NIC khác.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-config
  namespace: issues
data:
  postgresql.conf: |
    listen_addresses = '127.0.0.1,10.200.29.1'
    port = 5432
    hba_file = '/etc/postgresql/pg_hba.conf'
    wal_level = replica
    max_wal_senders = 5
    max_replication_slots = 5
    hot_standby = on
    wal_keep_size = '256MB'
    max_slot_wal_keep_size = '1GB'
    max_wal_size = '512MB'
    shared_buffers = '128MB'
    password_encryption = 'scram-sha-256'
    logging_collector = off
  pg_hba.conf: |
    local all postgres trust
    local all all scram-sha-256
    host all all 127.0.0.1/32 scram-sha-256
    host issues issues_app 10.42.0.0/16 scram-sha-256
    host replication replicator 10.200.29.1/32 scram-sha-256
    host replication replicator 10.200.29.2/32 scram-sha-256
  init.sql: |
    \getenv app_password APP_DB_PASSWORD
    \getenv repl_password REPLICATION_PASSWORD
    CREATE ROLE issues_app LOGIN PASSWORD :'app_password';
    GRANT pg_read_all_stats TO issues_app;
    CREATE ROLE replicator LOGIN REPLICATION PASSWORD :'repl_password';
    ALTER DATABASE issues OWNER TO issues_app;
```

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres-config.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres-config.yaml
```

init.sql chỉ chạy khi PGDATA trống trên AWS; Azure clone không chạy initdb/init.sql.

Role app cần `pg_read_all_stats` để `/api/database/status` đọc được `pg_stat_wal_receiver.status`; thiếu quyền có thể báo replication_connected=false dù standby đang streaming. Mẫu init.sql mới đã cấp quyền. **Nếu primary đã được init bằng mẫu cũ**, sửa ConfigMap không chạy lại init.sql; trên **AWS primary đã xác minh**, chạy một lần trước clone hoặc đợi thay đổi replay tới standby hiện có:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c 'GRANT pg_read_all_stats TO issues_app;'
```

Không chạy GRANT trên Azure standby và không xóa PVC để áp dụng init.sql. App vẫn không có superuser; quyền này cho đọc thống kê. [PostgreSQL statistics permissions](https://www.postgresql.org/docs/17/monitoring-stats.html).
 Giới hạn WAL không đảm bảo tổng đĩa không đầy: khi standby nghỉ lâu vượt WAL giữ lại cần clone lại. Mẫu không dùng persistent replication slot để tránh giữ WAL vô hạn; theo dõi disk và receiver thường xuyên.

### Manifest PostgreSQL AWS — PVC, StatefulSet và Service

Tạo `/opt/dcs29/postgres.yaml` bằng editor và dán YAML dưới. Thay **mọi POSTGRES_IMAGE_DIGEST** bằng cùng `postgres@sha256:...` đã chọn. K3s local-path lưu dữ liệu trên OS disk 30 GiB; PVC request 5Gi không phải quota cứng của filesystem. Chừa chỗ cho image/WAL và bản clone. Không có hai replica DB trên một VM.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: issues
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources:
    requests: {storage: 5Gi}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-headless
  namespace: issues
spec:
  clusterIP: None
  selector: {app: postgres}
  ports:
    - {name: postgres, port: 5432, targetPort: 5432}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: issues
spec:
  ports:
    - {name: postgres, port: 5432, targetPort: 5432}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: issues
spec:
  serviceName: postgres-headless
  replicas: 1
  selector:
    matchLabels: {app: postgres}
  template:
    metadata:
      labels: {app: postgres}
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      terminationGracePeriodSeconds: 60

      initContainers:
        - name: volume-permissions
          image: POSTGRES_IMAGE_DIGEST
          command: ["bash", "-ec", "mkdir -p /var/lib/postgresql/data/pgdata; chown -R 999:999 /var/lib/postgresql/data"]
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql/data}
      containers:
        - name: postgres
          image: POSTGRES_IMAGE_DIGEST
          args: ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
          envFrom:
            - secretRef: {name: postgres-auth}
          env:
            - {name: POSTGRES_USER, value: postgres}
            - {name: POSTGRES_DB, value: issues}
            - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
          resources:
            requests: {cpu: 200m, memory: 256Mi}
            limits: {cpu: "1", memory: 1Gi}
          startupProbe:
            exec:
              command: ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "issues"]
            periodSeconds: 5
            failureThreshold: 60
          readinessProbe:
            exec:
              command: ["pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "issues"]
            periodSeconds: 5
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql/data}
            - {name: config, mountPath: /etc/postgresql, readOnly: true}
            - {name: config, mountPath: /docker-entrypoint-initdb.d, readOnly: true}
            - {name: repl-pass, mountPath: /var/lib/postgresql/repl}
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: postgres-data}
        - name: config
          configMap: {name: postgres-config}
        - name: repl-pass
          emptyDir: {}
```

**AWS:** wg0 phải lên trước apply; primary initdb lần đầu. **Azure:** AWS phải có schema Issues và quyền replication trước apply; init container clone trước khi start DB. Không chạy base backup riêng vào host filesystem.

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres.yaml
sudo k3s kubectl -n issues get pods,pvc,svc -o wide
sudo k3s kubectl -n issues rollout status statefulset/postgres --timeout=300s
sudo k3s kubectl -n issues logs postgres-0 --tail=100
```

Azure clone lỗi thì xem `sudo k3s kubectl -n issues logs postgres-0 -c base-backup`. Partial copy được giữ để kiểm tra; tạo PVC mới khi cần clone lại, không xóa PGDATA bằng lệnh tự động.

Sau PVC Bound, giữ PV khi xóa nhầm PVC bằng Retain:

```bash
PV_NAME=$(sudo k3s kubectl -n issues get pvc postgres-data -o jsonpath='{.spec.volumeName}')
test -n "$PV_NAME"
sudo k3s kubectl patch pv "$PV_NAME" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
sudo k3s kubectl get pv "$PV_NAME"
df -h /
```

DB chỉ bind localhost/wg0; Service postgres không có selector, được nối tới tunnel IP qua EndpointSlice dưới. Không dùng IP NIC của node vì DB không bind NIC đó. Service headless chỉ định danh StatefulSet, không dùng làm DSN app.

Tạo `/opt/dcs29/postgres-endpoint.yaml`:

```yaml
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: postgres-tunnel
  namespace: issues
  labels:
    kubernetes.io/service-name: postgres
addressType: IPv4
ports:
  - {name: postgres, protocol: TCP, port: 5432}
endpoints:
  - addresses: ["10.200.29.1"]
    conditions: {ready: true}
```

```bash
sudo k3s kubectl apply -f /opt/dcs29/postgres-endpoint.yaml
```

Service không selector nên EndpointSlice thủ công không tự phản ánh pod Ready; app readiness và writer gate kiểm tra DB thực tế. Không publish NodePort/LoadBalancer.

### Tạo DATABASE_URL chính xác cho app

Sau khi DB primary/schema hoặc standby clone sẵn sàng, trên SSH mỗi VM:

```bash
cd /opt/dcs29
test -f postgres.env || { echo 'Thiếu postgres.env: dừng trước khi tạo DSN'; exit 1; }
umask 077
set -a
. ./postgres.env
set +a
printf 'DATABASE_URL=postgresql://issues_app:%s@postgres.issues.svc.cluster.local:5432/issues\n' "$APP_DB_PASSWORD" > secret.env
chmod 600 secret.env
sudo k3s kubectl -n issues create secret generic issues-db --from-env-file=secret.env --dry-run=client -o yaml | sudo k3s kubectl apply -f -
unset POSTGRES_PASSWORD APP_DB_PASSWORD REPLICATION_PASSWORD
```

Chỉ source file do chính bạn tạo với cấu trúc KEY=hex như trên. App dùng user issues_app, không superuser postgres. Trên AWS, apply app rồi bootstrap schema bằng lệnh `database.issues.initialize()` đã ghi dưới; app Running nhưng chưa Ready là bình thường trước bootstrap. Azure lấy schema qua clone, không initialize.

Sau khi tạo Deployment app ở phần bên dưới, kiểm tra từ **pod app** để xác minh routing pod→Service→DB; bỏ qua lệnh exec này khi Deployment chưa tồn tại:

```bash
sudo k3s kubectl -n issues exec deployment/issues-app -- python -c "from database.connection import connect; db=connect(); print(db.execute('SELECT pg_is_in_recovery() AS recovery').fetchone()); db.close()"
```

Nếu pg_hba báo nguồn khác 10.42.0.0/16 do SNAT, ghi source từ log, thêm rule **đúng nguồn nội bộ** cho database issues/user issues_app rồi restart DB có kiểm soát; không đổi host all all 0.0.0.0/0.


### 9.1. Thông số, mạng riêng và manifest PostgreSQL primary

**Trạng thái:** mẫu WireGuard/ConfigMap/PVC/StatefulSet ở mục cấu hình thực tế phía trên cho phép tự tạo các file cần dùng. Chưa nghiệm thu trên VM của bạn; các mục bên dưới giải thích vai trò và kiểm tra. Không apply lần nữa nếu đã triển khai cùng manifest.

1. Trên từng SSH, chạy `sudo k3s kubectl get nodes -o wide`, `free -h`, `df -h /`.
2. Ghi node/VM ID, disk/path, K3s version, app digest và PostgreSQL image/version vào bảng thông số.
3. Chốt namespace `issues`, StatefulSet DB `postgres`, app Deployment `issues-app`, app Service `issues-app` để các lệnh kiểm tra thống nhất.
4. Chốt endpoint DB giữa cloud và phương án VPN/tunnel có xác thực. VPC/VNet private IP và ClusterIP không tự định tuyến xuyên cloud.
5. Kiểm tra endpoint DB truy cập được từ pod, không chỉ SSH host. Đầu cuối phải dùng được theo cả hai chiều sau failback; 5432/NodePort không mở cho Internet.

Tạo cấu hình đã được review theo bảng dưới. Mẫu cấu hình thực tế phía trên dùng local-path/OS disk và tunnel IP đã chốt. Thay các thông số nếu mạng/storage của bạn khác trước khi apply.

| Thành phần | AWS | Azure |
|---|---|---|
| PostgreSQL | StatefulSet một replica primary ban đầu | StatefulSet một replica standby ban đầu |
| Image | PostgreSQL 17, ghim cùng bản/digest | Cùng PostgreSQL image |
| PVC | Storage riêng trên EBS bền vững | Storage riêng trên managed disk bền vững |
| PGDATA | Thư mục thuộc PVC đã xác minh | Thư mục mới/trống cho base backup |
| Service app → DB | ClusterIP nội bộ | ClusterIP nội bộ |
| Replication endpoint | Endpoint được định tuyến qua kết nối riêng | Endpoint riêng để AWS rejoin sau này |
| Secret | Password app và user replication | Cùng user replication cần thiết, DSN app riêng |
| WAL | wal_level=replica, sender đủ cho standby/base backup | hot_standby=on, primary_conninfo trỏ AWS |

PVC local-path của K3s là dữ liệu trên node, không tự sao chép hoặc thay thế backup. Ghi storageClass/PV/path/reclaim policy; xóa PVC có thể xóa dữ liệu tùy chính sách. Không đặt PostgreSQL trên temporary disk Azure. Giữ dư RAM cho OS/K3s, đo requests/limits trước khi tăng app replica.

Khi manifest đã thực sự tồn tại trên VM, chạy dry-run và kiểm tra trước khi apply. Ví dụ **sau khi đã tạo file từ mẫu phía trên** `/opt/dcs29/postgres.yaml`:

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres.yaml
sudo k3s kubectl -n issues get statefulset,pods,svc,pvc -o wide
sudo k3s kubectl -n issues describe statefulset postgres
```

Trên AWS tạo primary trước. Azure chưa chạy initdb độc lập; hoàn tất schema/base backup ở các mục sau trước khi khởi động standby. Nếu manifest và kết nối riêng chưa hoàn thiện, dừng ở đây.

**Phạm vi AWS:** tạo StatefulSet primary một replica, PVC trên EBS/OS disk bền vững, Service nội bộ và endpoint replication đi qua tunnel. Endpoint AWS phải truy cập được từ pod Azure sau khi Azure đã có VM/K3s. AWS có thể triển khai primary/app trước; phần kiểm tra sender streaming chỉ thực hiện khi Azure standby kết nối.

### 9.2. Khởi tạo primary và quyền replication

1. AWS: đợi pod `postgres-0` hoạt động, PVC Bound; xem log và PostgreSQL version.
2. Tạo database/tài khoản app theo Secret đã chốt; không dùng password local demo trên VM công khai.
3. Bật WAL replication, hot standby và max_wal_senders đủ cho base backup/streaming. Cả hai phía phải có thông số tương thích khi đổi role.
4. Tạo user replication riêng; giới hạn pg_hba và firewall theo nguồn thực tế qua tunnel, dùng xác thực phù hợp.
5. Kiểm tra từ pod Azure tới endpoint AWS; đừng mở 5432 cho 0.0.0.0/0 để xử lý timeout.

Trên SSH AWS, dùng tài khoản quản trị DB thực tế thay `DB_ADMIN_USER`:

```bash
sudo k3s kubectl -n issues logs postgres-0 --tail=100
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT version(), pg_is_in_recovery();'
```

Kỳ vọng recovery=false. Lệnh không tạo credential; phần CREATE ROLE/database cần thực hiện qua kênh quản trị có bảo vệ, không ghi password trong tài liệu/history.

### 9.3. Secret AWS và Deployment/Service Issues

Trên mỗi SSH: tạo `/opt/dcs29/secret.env` bằng editor, chỉ chứa `DATABASE_URL=postgresql://...` tới Service DB trong cluster tương ứng. Đặt quyền 600, không commit/copy vào image hoặc chụp màn hình giá trị. Tạo Secret:

```bash
cd /opt/dcs29
chmod 600 secret.env
sudo k3s kubectl -n issues create secret generic issues-db --from-env-file=secret.env --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Output lệnh chỉ báo kết quả apply, nhưng YAML giữa pipeline chứa dữ liệu Secret; không chạy riêng bước generate để lưu vào repo. DSN của app không dùng cổng DB localhost trên laptop.

Tạo `/opt/dcs29/app.yaml`, thay image/digest, environment và region trước khi apply:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: issues-app
  namespace: issues
spec:
  replicas: 1
  selector:
    matchLabels:
      app: issues-app
  template:
    metadata:
      labels:
        app: issues-app
    spec:
      containers:
        - name: app
          image: GHCR_IMAGE@sha256:IMAGE_DIGEST
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: APP_ENV
              value: aws
            - name: APP_REGION
              value: AWS_REGION
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: issues-db
                  key: DATABASE_URL
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          livenessProbe:
            httpGet:
              path: /health/live
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /api/database/status
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: issues-app
  namespace: issues
spec:
  selector:
    app: issues-app
  ports:
    - name: http
      port: 80
      targetPort: http
```

Azure đổi APP_ENV thành azure, APP_REGION thành Location thật; giữ image digest. Requests/limits trên là điểm bắt đầu cho app, cần đo chứ không cam kết chịu tải. Package private cần imagePullSecret được tạo riêng; mẫu trên dành cho package public.

Readiness `/api/database/status` cho phép primary và standby kết nối được tham gia Service. Nó chưa kiểm tra toàn bộ schema/dữ liệu, nên vẫn phải nghiệm thu `/api/issues` sau bootstrap. `/health/database` dành cho writer gate, **không** dùng làm readiness/liveness của Service đọc standby vì standby khỏe trả 503.

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/app.yaml
sudo k3s kubectl apply -f /opt/dcs29/app.yaml
sudo k3s kubectl -n issues get pods -l app=issues-app
```

AWS: app có thể chưa Ready khi schema chưa tồn tại; đợi container Running rồi bootstrap ở mục schema bên dưới trước khi chờ rollout. Azure: schema phải đến qua base backup; không bootstrap riêng trên standby.

### 9.4. Bootstrap schema — chỉ chạy AWS primary

Khi container app Running và DSN trỏ primary, trong SSH AWS:

```bash
sudo k3s kubectl -n issues exec deployment/issues-app -- python -c "from database.issues import initialize; initialize()"
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
```

Lệnh dùng DATABASE_URL trong Secret đã truyền vào pod. Bootstrap tasks chỉ khi cần demo cũ. App không tự init schema khi khởi động; không chạy lệnh này trên Azure standby.

### 9.5. Kiểm tra kết nối app trước Ingress

Để kiểm tra Service trước khi ingress, mở một SSH riêng trên từng VM:

```bash
sudo k3s kubectl -n issues port-forward service/issues-app 18080:80 --address 127.0.0.1
```

Trong SSH thứ hai trên **cùng VM**:

```bash
curl -fsS http://127.0.0.1:18080/version
curl -fsS http://127.0.0.1:18080/api/database/status
curl -fsS http://127.0.0.1:18080/api/issues
```

Ctrl+C để dừng port-forward sau kiểm tra. Không mở 18080 trên cloud firewall. GĐ5 sẽ tạo Ingress chính thức.

### 9.6. Tạo Ingress HTTP AWS

Sau khi Service/DB đọc được, trên từng VM tạo `/opt/dcs29/ingress-http.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: issues-http
  namespace: issues
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  ingressClassName: traefik
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: issues-app
                port:
                  number: 80
```

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/ingress-http.yaml
sudo k3s kubectl apply -f /opt/dcs29/ingress-http.yaml
sudo k3s kubectl -n issues get ingress
```

Cổng 80 phải đã mở ở bước firewall trong tài liệu này. Trên laptop kiểm tra `http://AWS_PUBLIC_IP/issues.html`, Azure tương ứng và `/version`, `/api/database/status`, `/api/issues`. Không mở app 8080 trực tiếp. HTTP chỉ dùng dữ liệu demo; GĐ5 cấp TLS rồi cập nhật Ingress HTTP thành redirect HTTPS.

### 9.7. Kiểm tra primary, WAL sender và bảo vệ storage

Trên SSH AWS, thay DB_ADMIN_USER bằng tài khoản quản trị thực tế:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT pg_is_in_recovery(); SELECT state, sent_lsn, replay_lsn FROM pg_stat_replication;'
```

AWS phải recovery=false. Chưa có Azure standby thì pg_stat_replication có thể chưa có dòng; sau khi hoàn thành hướng dẫn Azure, sender phải streaming và replay_lsn tiến triển.

Không xóa PVC/PV hoặc PGDATA để xử lý lỗi pod. Ghi volume/path/reclaim policy, giữ bản cũ khi rejoin và sao lưu trước thay đổi. PVC local-path không tự replicate. Hai origin chỉ được nghiệm thu đồng bộ sau khi Azure hoàn tất base backup và dữ liệu Issues khớp.

## 10. Kiểm tra app AWS sau khi hoàn tất phần database ở trên

SSH AWS:

```bash
sudo k3s kubectl -n issues get pods,svc,pvc -o wide
sudo k3s kubectl -n issues get deployment issues-app -o jsonpath='{.spec.template.spec.containers[0].image}'
sudo k3s kubectl -n issues logs deployment/issues-app --tail=100
```

Sau ingress HTTP, PowerShell laptop:

```powershell
Invoke-RestMethod "http://AWS_PUBLIC_IP/version"
Invoke-RestMethod "http://AWS_PUBLIC_IP/api/database/status"
Invoke-RestMethod "http://AWS_PUBLIC_IP/api/issues"
Test-NetConnection AWS_PUBLIC_IP -Port 8080
```

Mở http://AWS_PUBLIC_IP/issues.html. Environment aws, region đúng, commit khớp image, DB primary/writable và API đọc được schema; 8080 không public. /health/ready dataset không chứng minh DB ghi được; dùng /health/database cho writer readiness.

## 11. Restart, reboot và xử lý lỗi

Nếu đã bật GĐ6B, Disable EventBridge rule và xác nhận mọi execution/SSM command terminal trước các lệnh bảo trì bên dưới.

Khi app đã triển khai:

```bash
sudo k3s kubectl -n issues rollout restart deployment/issues-app
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
```

Đây là restart app, không promote DB. Nếu đã bật GĐ6B, Disable EventBridge rule và xác nhận mọi execution/SSM command terminal trước bảo trì/reboot. Console → Instances → Instance state → Reboot instance; đợi status checks, SSH lại, xác nhận writer và chạy block phê duyệt startup guard ở mục 9 trước kiểm tra K3s/node/pods/PVC/API. Chỉ Enable rule khi baseline và CloudWatch alarm OK; nếu đã có incident thì hoàn tất rejoin/failback/rearm trước. Không xóa PVC để sửa lỗi pod.

| Lỗi | Kiểm tra |
|---|---|
| Node NotReady | systemctl/journalctl, disk/RAM, events |
| ImagePullBackOff | Digest, visibility, imagePullSecret, outbound |
| CrashLoopBackOff | logs/describe pod, DSN/Secret, memory limit |
| HTTP 404/502/503 | Ingress Host, Service endpoints, pod Ready, DB/schema |
| DB offline | Pod/PVC, DSN, Service/mạng riêng, pg_hba |

## 12. Evidence, chi phí và rollback

Giữ ảnh các bước Console 2–6, K3s version/node/image/PVC, API và reboot. Rollback app bằng digest đã nghiệm thu khi schema tương thích; không đổi/xóa storage để rollback code.

Stop instance khi nghỉ; EBS/public IPv4 có thể vẫn tính phí. Nếu đã failover, AWS bật lại phải bị chặn primary cũ và rejoin trước failback. Không chỉ Start VM rồi để nó tự ghi. [AWS stop/start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html).

## 13. Checklist

- [ ] Region/VPC/subnet/IGW, SG, key và EIP đã kiểm tra.
- [ ] t3.medium, Ubuntu x86_64, gp3 30 GiB ban đầu đúng.
- [ ] SSH /32; DB/K3s API/NodePort không mở Internet.
- [ ] K3s active, node Ready sau reboot; ghi CPU credit mode.
- [ ] PostgreSQL primary/PVC, schema, Secret/app và HTTP Ingress AWS đã kiểm tra.
- [ ] Kiểm tra sender streaming và dữ liệu sau khi Azure standby hoàn tất.

Tiếp [GĐ4 Azure](GIAI_DOAN_04_AZURE_PORTAL.md); HTTPS ở GĐ5.

## Sao lưu trước khi đổi storage hoặc dọn VM

Trên primary đang phục vụ, tạo logical dump trên host (không đặt trong PVC active); lệnh mẫu dùng user postgres qua Unix socket trong pod:

```bash
sudo install -d -m 700 /opt/dcs29/backups
BACKUP_FILE="/opt/dcs29/backups/issues-$(date -u +%Y%m%dT%H%M%SZ).dump"
sudo bash -c 'umask 077; /usr/local/bin/k3s kubectl -n issues exec postgres-0 -- pg_dump -U postgres -d issues -Fc > "$1"' backup-save "$BACKUP_FILE"
sudo test -s "$BACKUP_FILE"
sudo k3s kubectl -n issues exec postgres-0 -- pg_restore --version
```

pg_dump không thay thế physical clone để tạo standby. Kiểm tra dump bằng cách copy vào một database tách biệt hoặc môi trường local và dùng pg_restore; không restore đè database active. Download bằng SCP sau khi copy file sang thư mục chỉ user quản trị đọc được, giữ bản ngoài VM trước destroy. Không đưa dump vào Git. Ghi file, thời gian, checksum và kết quả restore thử.

### Giới hạn log trên OS disk 30 GiB

Trên từng VM, thêm drop-in K3s; không ghi đè config.yaml đang có:

```bash
sudo install -d -m 700 /etc/rancher/k3s/config.yaml.d
sudo tee /etc/rancher/k3s/config.yaml.d/20-log-limits.yaml >/dev/null <<'EOF'
kubelet-arg:
  - container-log-max-size=10Mi
  - container-log-max-files=3
EOF
sudo install -d /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/20-dcs29-limits.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=256M
RuntimeMaxUse=64M
EOF
sudo systemctl restart systemd-journald
```

Nếu đã có kubelet-arg trong config khác, gộp đầy đủ các giá trị vào một cấu hình thay vì làm mất flag cũ. K3s áp dụng log limits sau restart có kiểm soát, không restart primary trong lúc base backup hoặc chuyển role. Với startup guard đã phê duyệt trong boot hiện tại:

```bash
sudo systemctl restart k3s
sudo k3s kubectl get pods -A
sudo journalctl --disk-usage
df -h /
sudo du -xhd1 /var/lib/rancher/k3s /var/log /opt/dcs29
```

Không prune/xóa PVC hoặc data dir của containerd trong lúc workload chạy. Log giới hạn không giới hạn WAL; theo dõi lag/đĩa và clone lại standby nếu WAL cần thiết đã mất. Backup dài hạn phải copy ngoài VM.


### Kiểm tra dump bằng restore vào database tách biệt

Chạy trên primary đã có BACKUP_FILE từ block backup trước; bảo đảm đủ đĩa giữ thêm bản restore. Tên issues_restore_check phải chưa tồn tại, createdb sẽ dừng nếu đã có:

```bash
set -o pipefail
sudo k3s kubectl -n issues exec postgres-0 -- createdb -U postgres issues_restore_check || { echo 'Không tạo được database kiểm thử: dừng, không restore vào DB đã có'; exit 1; }
sudo cat "$BACKUP_FILE" | sudo k3s kubectl -n issues exec -i postgres-0 -- pg_restore -U postgres -d issues_restore_check --no-owner --exit-on-error || { echo 'Restore lỗi: giữ dump và DB thử để kiểm tra'; exit 1; }
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues_restore_check -c 'SELECT count(*) FROM issues; SELECT count(*) FROM comments; SELECT count(*) FROM issue_history;'
sudo sha256sum "$BACKUP_FILE"
```

Đối chiếu ID/nội dung với source. Restore check không thay đổi DATABASE_URL app, không promote standby. Sau khi ghi kết quả, nếu không cần DB thử nữa, chỉ xóa đúng database thử đã tạo:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- dropdb -U postgres issues_restore_check
```

Để download backup qua SCP, copy thành file user quản trị sở hữu quyền 600; chỉ thực hiện trong thư mục backup của lab:

```bash
sudo cp "$BACKUP_FILE" /opt/dcs29/issues-download.dump
sudo chown "$USER:$USER" /opt/dcs29/issues-download.dump
chmod 600 /opt/dcs29/issues-download.dump
```

PowerShell laptop, thay user/IP/key của VM primary hiện tại:

```powershell
scp -i PATH_TO_PRIMARY_KEY PRIMARY_ADMIN_USER@PRIMARY_PUBLIC_IP:/opt/dcs29/issues-download.dump "$env:USERPROFILE/.dcs29-private/issues-backup.dump"
```

Giữ bản ngoài VM và checksum trước khi destroy; nếu backup mới thì chọn tên file laptop mới, không ghi đè bản đã nghiệm thu.
