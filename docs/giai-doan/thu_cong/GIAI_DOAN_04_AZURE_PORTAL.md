# GĐ4 — Azure Standard_B2s, K3s và standby PostgreSQL

Xem [sơ đồ kiến trúc và workflow demo](../../../images/ARCHITECTURE.md) để đối chiếu vị trí Azure VM trong hệ thống hai cloud.

**Cách dùng:** trước khi bắt đầu, ghi `GHCR_IMAGE`, `IMAGE_DIGEST`, `AZURE_REGION` vào [bảng thông số](THONG_SO_TRIEN_KHAI.md). Sau khi tạo VM, ghi `AZURE_PUBLIC_IP`. Mỗi ký hiệu phải được thay bằng giá trị mới của chính lần triển khai này trước khi chạy lệnh.

**Thứ tự hai lượt:** có thể tạo Azure VM/K3s/namespace/guard trước khi AWS có database để lấy IP/key WireGuard. Khi đã cấu hình tunnel hai phía, hoàn tất AWS primary/schema/app theo Console guide rồi quay lại phần PostgreSQL Azure. Block tạo credential ghi “Chỉ AWS” dưới đây chỉ là tham chiếu cho primary chưa có file; nếu AWS đã làm, dùng file hiện có, không tạo lại.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Dựng Azure VM, K3s, PostgreSQL standby và Issues Azure. |
| Chuẩn bị | AWS primary/schema sẵn sàng trước bước clone; hai peer WireGuard đã kết nối. |
| Hoàn tất khi | Standby streaming; app Azure đúng digest và đọc được dữ liệu. |

**Luồng thao tác:** [2. Kiểm tra trước khi tạo](#2-kiểm-tra-trước-khi-tạo) → [4. Tạo Ubuntu VM bằng Azure Portal](#4-tạo-ubuntu-vm-bằng-azure-portal) → [6. Cài K3s trên Azure — trong SSH](#6-cài-k3s-trên-azure--trong-ssh) → [7. Namespace, mạng riêng và PostgreSQL](#7-namespace-mạng-riêng-và-postgresql) → [7A. Cấu hình WireGuard và PostgreSQL thực tế](#7a-cấu-hình-wireguard-và-postgresql-thực-tế) → [8. Secret, image và ứng dụng Azure](#8-secret-image-và-ứng-dụng-azure) → [9. Kiểm tra và so với AWS](#9-kiểm-tra-và-so-với-aws) → [11. Bằng chứng và checklist](#11-bằng-chứng-và-checklist).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Mô hình và vùng triển khai

```text
Internet → Public IP tĩnh → Network Security Group (80) → Azure VM Ubuntu
                                                    └─ K3s: Traefik → Service → Issues → PostgreSQL standby
```

Subscription, policy và quota quyết định region/VM size có thể dùng. Chọn một region được phép và ghi **mã** thành `AZURE_REGION`; đặt `APP_REGION` bằng **Location thực của VM**. VM, VNet, NIC, NSG, Public IP và disk phải dùng vùng phù hợp. Region của Resource Group chỉ là metadata; nếu `dcs29-rg` đã tồn tại, kiểm tra tài nguyên bên trong rồi mới dùng lại. [Microsoft: VM quota](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).

GĐ4 dùng **HTTP cổng 80** để kiểm tra origin như AWS GĐ3; domain chung và HTTPS thuộc GĐ5. Để chứng minh DNS failover trong GĐ6, **cả AWS và Azure VM phải đang chạy, healthy trước khi gây lỗi AWS**. Có thể deallocate Azure VM ngoài giờ demo nhưng lúc đó nó không làm standby tự động.

**B2s/disk:** Bv1 có thông báo retirement, kiểm tra availability/quota/lifecycle trước tạo. Managed disk không thể thu nhỏ trực tiếp. [Azure Bv1](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bv1-series), [disk FAQ](https://learn.microsoft.com/en-us/azure/virtual-machines/faq-for-disks).

## 2. Kiểm tra trước khi tạo

1. Mở **Subscriptions**, xác nhận subscription bạn sẽ dùng. Nếu có policy **Allowed resource deployment regions**, xem danh sách vùng được phép của chính subscription đó.
2. Mở **Resource groups → dcs29-rg** nếu đã có; xác nhận quyền sở hữu và tài nguyên bên trong. Không xóa Resource Group đang dùng.
3. Mở **Subscriptions → Usage + quotas**, lọc `AZURE_REGION`, kiểm tra quota vCPU và kích thước VM dự định. Portal còn có thể báo thiếu capacity dù quota đủ; khi đó thử region khác hỗ trợ Standard_B2s; nếu cần đổi size, chốt lại trước và cập nhật `AZURE_REGION`. [Microsoft: quota và capacity](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).
4. Dự tính phí VM, managed disk và Public IP trong [Azure Pricing Calculator](https://azure.microsoft.com/en-us/pricing/calculator/). Sau khi tạo Resource Group ở mục 3, có thể đặt budget cảnh báo chi phí; budget không tự dừng VM.

## 3. Tạo Resource Group

Trong Azure Portal, tìm **Resource groups → Create**. Điền:

| Trường          | Giá trị                                                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Subscription      | Subscription của bạn đã kiểm tra ở mục 2 |
| Resource group    | `dcs29-rg`                                                                                                                    |
| Region            | Có thể chọn `AZURE_REGION`; ghi lựa chọn thực tế của Resource Group |
| Tags (nếu dùng) | `Project=dcs29`, `Owner=<tên nhóm>`; không đưa username cá nhân vào tài liệu/ảnh                                 |

Chọn **Review + create → Create**, đợi thông báo tạo thành công, rồi vào **Resource groups → dcs29-rg** để xác nhận nhóm tài nguyên xuất hiện. Nếu `dcs29-rg` **đã tồn tại**, mở nó và kiểm tra subscription, danh sách tài nguyên; **không tạo thêm nhóm trùng tên hoặc xóa nhóm đang dùng**. Resource Group có thể ở vùng khác với VM vì vùng của nó lưu metadata; VM và tài nguyên mạng/ổ đĩa vẫn phải tạo ở vùng được policy cho phép. [Microsoft: tạo Resource Group trong Portal](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resource-groups-portal).

Sau khi có `dcs29-rg`, vào **Resource groups → dcs29-rg → Cost Management → Budgets → Add** nếu muốn đặt cảnh báo chi phí. Budget không tự dừng VM.

## 4. Tạo Ubuntu VM bằng Azure Portal

Vào **Virtual machines → Create → Azure virtual machine**. Điền các tab; tên mục có thể khác đôi chút theo phiên bản Portal. [Microsoft: tạo Linux VM](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-portal).

| Tab/trường                             | Giá trị cho demo                                                                                                                                                         |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Basics → Subscription                   | Subscription của bạn đã kiểm tra |
| Basics → Resource group                 | Chọn`dcs29-rg` đã tạo/kiểm tra ở mục 3                                                                                                                            |
| Basics → Virtual machine name           | `dcs29-azure-vm`                                                                                                                                                         |
| Basics → Region                         | Vùng được phép đã ghi là `AZURE_REGION`; nếu đổi vùng, cập nhật bảng thông số và `APP_REGION` ở mục 7 |
| Basics → Availability options           | **No infrastructure redundancy required** cho demo một VM                                                                                                           |
| Basics → Security type                  | **Standard** nếu tùy chọn khác gây hạn chế/quota; ghi lựa chọn thực tế                                                                                    |
| Basics → Image                          | **Ubuntu Server 24.04 LTS x64**; cần x86-64 vì image GHCR GĐ2 là `linux/amd64`                                                                                 |
| Basics → Size                           | **Standard_B2s**, 2 vCPU / 4 GiB theo cấu hình đã chốt; không chọn Arm. |
| Administrator account                    | **SSH public key**, username Linux tự đặt; lưu private key trên máy cá nhân, không đưa vào repo                                                          |
| Inbound ports                            | SSH (22) để vào máy; sau khi tạo, giới hạn source về IP công khai của bạn                                                                                       |
| Disks → OS disk                         | **Standard SSD**, chọn 30 GiB hoặc lớn hơn nếu OS image yêu cầu; không giảm đĩa đang có dữ liệu, không lưu DB trên temporary/ephemeral disk                                                                     |
| Networking → Virtual network/subnet     | Tạo VNet và subnet mới do Portal đề xuất hoặc dùng VNet của nhóm đã kiểm tra; Azure không có “default VPC” như AWS                                       |
| Networking → Public IP                  | **Standard, Static, IPv4** để IP origin không đổi sau stop/start; kiểm tra phí                                                                                |
| Networking → NIC network security group | **Basic** để Portal tạo NSG, rồi kiểm tra inbound rules sau khi tạo                                                                                            |
| Management                               | Boot diagnostics theo nhu cầu; kiểm tra phí của tùy chọn bổ sung trước khi bật                                                                                   |

Chọn **Review + create → Create**. Nếu báo `RequestDisallowedByAzure`, xem lại region của **mọi tài nguyên** được tạo cùng VM. Nếu báo quota/capacity, kiểm tra Standard_B2s tại region khác được phép; nếu cần đổi size, chốt lại trước và đổi các bước tiếp theo cho đúng vùng. Chỉ đánh dấu đã tạo khi Portal báo **Deployment succeeded**.

## 5. Mạng và SSH

### 5.1. Đối chiếu với tab **Networking** lúc tạo VM

Tab **Networking** khi tạo VM có các trường **Virtual network**, **Subnet**, **Public IP**, **NIC network security group**, **Public inbound ports** và **Select inbound ports**. Chọn/kiểm tra như sau:

| Trường trên Portal | Chọn gì | Sau khi tạo cần làm gì |
|---|---|---|
| **Virtual network** | Có thể để VNet mới Portal đề xuất, ví dụ `vnet-AZURE_REGION-1` | VNet phải cùng region với **VM**; region của Resource Group có thể khác. |
| **Subnet** | Để subnet mới trong VNet đó, ví dụ `snet-AZURE_REGION-1 - 172.16.0.0/24` | Không cần tạo thêm subnet cho một VM. |
| **Public IP** | Chọn IP mới; bấm **Create new** để kiểm tra **Standard / Static / IPv4** | Dùng IP này cho SSH và bản ghi DNS failover. Chỉ nhìn tên IP trong ô chọn chưa xác nhận được nó là Static. |
| **NIC network security group** | **Basic** | Portal tạo NSG để quản lý các cổng vào VM. |
| **Public inbound ports** | **Allow selected ports** | Mở danh sách cổng ở dòng tiếp theo. |
| **Select inbound ports** | **SSH (22)** | Cổng 80 sẽ thêm sau khi tạo VM; không cần chọn Load balancing. |

Sau khi tạo, kiểm tra **VM → Overview → Location** và đối chiếu với `AZURE_REGION`; VM và VNet phải cùng region. Ở mục 9, `/version` phải trả `region=AZURE_REGION`. [Microsoft: VM và VNet cùng region](https://learn.microsoft.com/en-us/azure/virtual-network/network-overview).

### 5.2. Sau khi bấm **Review + create**

1. Đợi **Validation passed**, kiểm tra lại VM size, region, OS disk và Public IP rồi bấm **Create**. Nếu đã chọn **Generate new key pair** ở tab Basics, cửa sổ **Generate new key pair** sẽ hiện ra lúc này; bấm **Download private key and create resource**. Lưu file `.pem` trên máy cá nhân, không đưa vào repo. [Microsoft: tạo VM và tải SSH key](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-portal).
2. Đợi **Deployment succeeded** → **Go to resource**. Ở trang **Overview** của VM, kiểm tra **Status: Running**, sao chép **Public IP address**. Mở resource Public IP để xác nhận **Allocation method: Static** nếu chưa kiểm tra ở bước tạo.
3. Vào **VM → Networking → Network settings** (giao diện khác có thể ghi **Networking**). Tìm và mở **Network security group** gắn với NIC, rồi vào **Settings → Inbound security rules**. Đây là nơi chỉnh rule cổng 22 và thêm cổng 80; **không chỉnh dải địa chỉ của Subnet** để mở cổng.

| Trong**Inbound security rules**               | Thao tác cụ thể                                                                                                                                                                                                                                                                                                                                                                                                    |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rule**SSH / TCP 22** đã được Portal tạo | Mở rule đó →**Edit**. Đổi **Source** thành **IP Addresses**, nhập IP công khai hiện tại của máy bạn dưới dạng `x.x.x.x/32` vào **Source IP addresses/CIDR ranges** → **Save**. Không dùng IP nội bộ `172.16.0.x`. Nếu chưa biết IP công khai, xem trên trang kiểm tra IP của trình duyệt hoặc mục **My IP address** nếu Portal hiển thị. |
| **HTTP / TCP 80**                             | Bấm**Add** → **Source: Any**, **Source port ranges: `*`**, **Destination: Any**, **Service: HTTP** hoặc **Protocol: TCP** với **Destination port ranges: 80**, **Action: Allow**, **Priority** là số chưa dùng (ví dụ `310`) → **Add**. Cổng này dùng cho ingress Traefik; thêm 443 ở GĐ5.                                                       |
| **8080 / 443**                                | Không thêm rule 8080 vì FastAPI qua Service nội bộ K3s. Cổng 443 chỉ mở khi cấu hình HTTPS ở GĐ5.                                                                                                                                                                                                                                                                                                          |

Không mở DB 5432, K3s API 6443 hoặc NodePort ra Internet. Rule tunnel chỉ cho peer AWS sau khi chốt mạng. Chỉ thay IP nguồn của rule SSH khi chắc chắn đó là IP công khai hiện tại của máy đang dùng; nhập sai sẽ khiến SSH bị chặn. [Microsoft: NSG](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview).

### 5.3. SSH từ máy cá nhân

Mở **PowerShell trên máy cá nhân** (không phải terminal trong Azure Portal). Trong lệnh mẫu dưới đây, thay `YOUR_WINDOWS_USER`, `YOUR_KEY.pem` và `203.0.113.10` bằng đường dẫn file `.pem` vừa tải và Public IP thật của VM; giữ đúng username đã chọn ở tab Basics, ví dụ `azureuser`:

```powershell
ssh -i "C:\Users\YOUR_WINDOWS_USER\Downloads\YOUR_KEY.pem" azureuser@203.0.113.10
```

Nếu SSH timeout, kiểm tra VM đang **Running**, Public IP, rule TCP 22 của NSG và IP nguồn `/32` của bạn. Nếu báo lỗi xác thực, kiểm tra username và đúng file `.pem` đã tải khi tạo VM.

## 6. Cài K3s trên Azure — trong SSH

Kiểm tra uname -m, nproc, free -h và df -h /. Kỳ vọng x86_64, 2 vCPU, gần 4 GiB RAM. Thay K3S_VERSION bằng **cùng release đã chọn trên AWS**:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
curl -sfL https://get.k3s.io -o /tmp/install-k3s.sh
sudo env INSTALL_K3S_VERSION='K3S_VERSION' sh /tmp/install-k3s.sh
sudo systemctl is-active k3s
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
```

Không dùng token/K3S_URL AWS; Azure là cluster độc lập. Nếu lỗi, chạy `sudo journalctl -u k3s -n 100 --no-pager` và `sudo k3s kubectl get events -A --sort-by=.metadata.creationTimestamp`. Không cài Docker/NGINX Compose cũ trên cùng cổng ingress.

## 7. Namespace, mạng riêng và PostgreSQL

```bash
sudo mkdir -p /opt/dcs29
sudo chown "$USER:$USER" /opt/dcs29
cd /opt/dcs29
sudo k3s kubectl create namespace issues --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Các bước PostgreSQL standby, Secret/app và Ingress được trình bày ngay bên dưới. AWS primary/schema phải hoàn tất theo hướng dẫn AWS Console trước khi clone Azure.

PVC không tự sao chép xuyên cloud. Không init Azure primary độc lập. Các mẫu ngay bên dưới tạo manifest/mạng riêng; hoàn thành chúng trước khi đánh dấu GĐ4 đạt. Script Compose local không phải điều phối cloud.


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

## 7A. Cấu hình WireGuard và PostgreSQL thực tế


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

### Cấu hình wg0 — SSH AZURE

Thay public IP và public key peer thật trước khi chạy. Private key được đọc vào file trong VM, không in ra terminal:

```bash
sudo bash -c 'set -euo pipefail; umask 077; test ! -e /etc/wireguard/wg0.conf; cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.200.29.2/24
ListenPort = 51820
PrivateKey = $(cat /etc/wireguard/private.key)

[Peer]
PublicKey = AWS_WIREGUARD_PUBLIC_KEY
Endpoint = AWS_PUBLIC_IP:51820
AllowedIPs = 10.200.29.1/32
PersistentKeepalive = 25
EOF'
sudo systemctl enable --now wg-quick@wg0
sudo wg show wg0
ip route get 10.200.29.1
ping -c 3 10.200.29.1
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

### PostgreSQL config chung — tạo file trên AZURE

Trên SSH, mở `nano /opt/dcs29/postgres-config.yaml`, dán YAML sau. Mỗi cloud có ConfigMap riêng; listen_addresses phải đúng tunnel IP máy đó. Mẫu cấp quyền app từ pod CIDR K3s mặc định `10.42.0.0/16`; nếu đã đổi cluster-cidr hãy đổi pg_hba cho đúng. DB dùng hostNetwork, chỉ bind localhost và wg0, không bind public/private NIC khác.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-config
  namespace: issues
data:
  postgresql.conf: |
    listen_addresses = '127.0.0.1,10.200.29.2'
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

### Manifest PostgreSQL AZURE — PVC, StatefulSet và Service

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
        - name: base-backup
          image: POSTGRES_IMAGE_DIGEST
          envFrom:
            - secretRef: {name: postgres-auth}
          env:
            - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
            - {name: SOURCE_DB_HOST, value: "10.200.29.1"}
          command: ["bash", "-ec"]
          args:
            - |
              install -d -o postgres -g postgres -m 700 /var/lib/postgresql/repl
              printf '%s:5432:*:replicator:%s\n' "$SOURCE_DB_HOST" "$REPLICATION_PASSWORD" > /var/lib/postgresql/repl/pgpass
              chown postgres:postgres /var/lib/postgresql/repl/pgpass
              chmod 600 /var/lib/postgresql/repl/pgpass
              export PGPASSFILE=/var/lib/postgresql/repl/pgpass
              if [ -s "$PGDATA/PG_VERSION" ]; then
                exit 0
              fi
              mkdir -p "$PGDATA"
              test -z "$(ls -A "$PGDATA")" || { echo 'Partial copy: use a new PVC'; exit 1; }
              chown postgres:postgres "$PGDATA"
              gosu postgres pg_basebackup -d "host=$SOURCE_DB_HOST port=5432 user=replicator passfile=$PGPASSFILE" -D "$PGDATA" -R -X stream -c fast
          volumeMounts:
            - {name: data, mountPath: /var/lib/postgresql/data}
            - {name: repl-pass, mountPath: /var/lib/postgresql/repl}
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
  - addresses: ["10.200.29.2"]
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


### 7.1. Thông số, mạng riêng và storage Azure

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

Chỉ apply manifest standby đã có bước clone PVC bằng init container/Job và không chạy initdb độc lập. Lệnh dry-run/apply đặt sau phần base backup ở mục 7.2.

AWS primary/schema phải tồn tại trước. Trên Azure chuẩn bị PVC và manifest init/base backup; chưa apply cấu hình khiến PostgreSQL initdb độc lập. Hoàn tất clone theo mục 7.2 rồi mới khởi động standby. Nếu manifest/mạng riêng chưa hoàn thiện, dừng ở đây.

### 7.2. Base backup từ AWS và khởi động standby

1. Xác minh storage Azure mới/trống, ghi PVC/path. Không xóa dữ liệu hiện có để ép clone.
2. Trong init Job/container có mount PVC và cùng PostgreSQL image, dùng pg_basebackup tới endpoint riêng AWS, user replication và credential từ Secret.
3. Chọn `-R` để tạo standby.signal/primary_conninfo, `-X stream` để nhận WAL và `-c fast` phù hợp demo; dùng đúng target PGDATA trên PVC. Lệnh phải được đặt vào manifest init đã review, không chạy từ laptop vào một thư mục tùy ý.
4. Hoàn tất clone rồi mới khởi động StatefulSet standby Azure; nếu copy dở, dừng và kiểm tra, không tự xóa thư mục không rỗng.
5. Triển khai app Azure theo mục 8 bên dưới; schema đã đến qua bản sao primary nên không init riêng.

Mẫu tham số trong init container, **chỉ sau khi mount PVC/Secret và endpoint đã chốt**, password qua PGPASSWORD hoặc passfile có quyền phù hợp:

```bash
pg_basebackup -h AWS_DB_PRIVATE_HOST -p DB_REPLICATION_PORT -U replicator -D PGDATA_TARGET -R -X stream -c fast
```

Thay mọi placeholder; ghi quyền sở hữu PGDATA theo user postgres của image. Không chạy lại base backup vào thư mục dữ liệu đang dùng. Nguồn: [pg_basebackup PostgreSQL 17](https://www.postgresql.org/docs/17/app-pgbasebackup.html).

Sau khi manifest `/opt/dcs29/postgres.yaml` đã được tạo/review và cơ chế base backup hoàn tất đúng PVC (hoặc init container đảm bảo clone trước PostgreSQL), trên SSH Azure:

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres.yaml
sudo k3s kubectl -n issues get statefulset,pods,svc,pvc -o wide
sudo k3s kubectl -n issues logs postgres-0 --tail=100
```

### 7.3. Xác nhận recovery và WAL receiver

Trên SSH Azure, thay DB_ADMIN_USER bằng tài khoản quản trị đã được sao chép từ primary:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT pg_is_in_recovery(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(); SELECT status FROM pg_stat_wal_receiver;'
```

Azure phải recovery=true, receiver streaming, WAL receive/replay tiến triển. Nếu recovery=false, dừng nghiệm thu: Azure chưa phải standby. Không promote hoặc bootstrap schema để chữa lỗi kết nối.

## 8. Secret, image và ứng dụng Azure

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
              value: azure
            - name: APP_REGION
              value: AZURE_REGION
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

Mẫu trên đã dùng APP_ENV=azure; thay AZURE_REGION bằng Location thật và giữ cùng image digest với AWS. Requests/limits trên là điểm bắt đầu cho app, cần đo chứ không cam kết chịu tải. Package private cần imagePullSecret được tạo riêng; mẫu trên dành cho package public.

Readiness `/api/database/status` cho phép primary và standby kết nối được tham gia Service. Nó chưa kiểm tra toàn bộ schema/dữ liệu, nên vẫn phải nghiệm thu `/api/issues` sau bootstrap. `/health/database` dành cho writer gate, **không** dùng làm readiness/liveness của Service đọc standby vì standby khỏe trả 503.

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/app.yaml
sudo k3s kubectl apply -f /opt/dcs29/app.yaml
sudo k3s kubectl -n issues get pods -l app=issues-app
```

Schema phải đến qua base backup; không chạy database.issues.initialize() trên Azure standby. Nếu app chưa Ready, kiểm tra Secret, Service DB, schema đã sao chép và trạng thái receiver.

Sau apply, chờ app Ready vì schema đã đến từ primary:

```bash
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
sudo k3s kubectl -n issues get deployment issues-app -o jsonpath='{.spec.template.spec.containers[0].image}'
```

### 8.1. Kiểm tra Service trước Ingress

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

### 8.2. Ingress HTTP Azure

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

Cổng 80 phải đã mở ở bước firewall trong tài liệu này. Trên laptop kiểm tra `http://AZURE_PUBLIC_IP/issues.html`, Azure tương ứng và `/version`, `/api/database/status`, `/api/issues`. Không mở app 8080 trực tiếp. HTTP chỉ dùng dữ liệu demo; GĐ5 cấp TLS rồi cập nhật Ingress HTTP thành redirect HTTPS.

### 8.3. Storage và dữ liệu cũ

Ghi PVC/PV/path/reclaim policy; không lưu PGDATA trên temporary disk Azure. Không xóa PVC active hoặc chạy lại base backup vào PGDATA đang dùng. Khi rejoin/failback dùng storage mới và giữ bản cũ đến khi dữ liệu đã được đối chiếu.

## 9. Kiểm tra và so với AWS

Sau ingress HTTP, laptop PowerShell:

```powershell
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/version'
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/api/database/status'
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/api/issues'
Test-NetConnection AZURE_PUBLIC_IP -Port 8080
```

Mở http://AZURE_PUBLIC_IP/issues.html. Environment azure, region đúng, commit giống AWS; DB standby/replication_connected, writable=false. 8080 không public. Tạo/sửa/bình luận/đóng issue trên AWS rồi đối chiếu cả dữ liệu/lịch sử Azure.

Nếu đã bật GĐ6B, Disable EventBridge rule và xác nhận mọi execution/SSM command terminal trước bảo trì/reboot. Reboot VM ở Portal, đợi Running, SSH lại, xác nhận writer và phê duyệt startup guard bằng block trong mục 7 trước kiểm tra K3s/node/pods/PVC và streaming. Chỉ Enable rule khi baseline và CloudWatch alarm OK; incident đã tồn tại phải hoàn tất rejoin/failback/rearm trước. Không xóa PVC để sửa pod. Nếu role đang đổi, theo GĐ6, không tự bật primary cũ.

## 10. Buổi demo và chi phí

Start cả hai VM, kiểm tra API/role/streaming trước thử. Azure deallocated không nhận WAL và không làm standby hoạt động. Sau demo Stop/Deallocate để ngừng compute; disk/IP có thể vẫn tính phí. [Azure states/billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

## 11. Bằng chứng và checklist

Giữ ảnh Portal các bước 2–5, size/OS/disk/VNet/subnet/NSG/Static IP, K3s version/image/PVC/role/WAL, issue/comments/history và reboot. Không đưa credential vào ảnh.

- [ ] Standard_B2s, Ubuntu x64; availability/quota/lifecycle đã kiểm tra.
- [ ] SSH /32; DB/API/NodePort không mở Internet.
- [ ] K3s độc lập Ready sau reboot; DB trên disk bền vững.
- [ ] App cùng digest, đúng region/cloud.
- [ ] AWS primary → Azure standby; đủ schema/dữ liệu và từ chối ghi.
- [ ] App đọc standby qua Service/Ingress được.

Tiếp [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) và GĐ6.

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
