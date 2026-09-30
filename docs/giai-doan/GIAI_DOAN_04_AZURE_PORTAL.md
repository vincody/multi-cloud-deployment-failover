# GĐ4 — Azure VM Ubuntu chạy Docker Compose

Xem [sơ đồ kiến trúc và workflow demo](../../images/ARCHITECTURE.md) để đối chiếu vị trí Azure VM trong hệ thống hai cloud.

**Cách dùng:** trước khi bắt đầu, ghi `GHCR_IMAGE`, `IMAGE_DIGEST`, `AZURE_REGION` vào [bảng thông số](THONG_SO_TRIEN_KHAI.md). Sau khi tạo VM, ghi `AZURE_PUBLIC_IP`. Mỗi ký hiệu phải được thay bằng giá trị mới của chính lần triển khai này trước khi chạy lệnh.

## 1. Mô hình và vùng triển khai

```text
Internet → Public IP tĩnh → Network Security Group (80) → Azure VM Ubuntu
                                                    └─ Docker Compose: NGINX (80) → FastAPI (8080 nội bộ)
```

Subscription, policy và quota quyết định region/VM size có thể dùng. Chọn một region được phép và ghi **mã** thành `AZURE_REGION`; đặt `APP_REGION` bằng **Location thực của VM**. VM, VNet, NIC, NSG, Public IP và disk phải dùng vùng phù hợp. Region của Resource Group chỉ là metadata; nếu `dcs29-rg` đã tồn tại, kiểm tra tài nguyên bên trong rồi mới dùng lại. [Microsoft: VM quota](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).

GĐ4 dùng **HTTP cổng 80** để kiểm tra origin như AWS GĐ3; domain chung và HTTPS thuộc GĐ5. Để chứng minh DNS failover trong GĐ6, **cả AWS và Azure VM phải đang chạy, healthy trước khi gây lỗi AWS**. Có thể deallocate Azure VM ngoài giờ demo nhưng lúc đó nó không làm standby tự động.

## 2. Kiểm tra trước khi tạo

1. Mở **Subscriptions**, xác nhận subscription bạn sẽ dùng. Nếu có policy **Allowed resource deployment regions**, xem danh sách vùng được phép của chính subscription đó.
2. Mở **Resource groups → dcs29-rg** nếu đã có; xác nhận quyền sở hữu và tài nguyên bên trong. Không xóa Resource Group đang dùng.
3. Mở **Subscriptions → Usage + quotas**, lọc `AZURE_REGION`, kiểm tra quota vCPU và kích thước VM dự định. Portal còn có thể báo thiếu capacity dù quota đủ; khi đó thử size/region khác được policy cho phép và cập nhật `AZURE_REGION`. [Microsoft: quota và capacity](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).
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
| Basics → Size                           | Bắt đầu**Standard_B1ms** (1 vCPU, 2 GiB) nếu Portal cho phép; có thể chọn B2s (2 vCPU, 4 GiB) nếu đo thiếu RAM và chấp nhận phí. Không chọn VM Arm. |
| Administrator account                    | **SSH public key**, username Linux tự đặt; lưu private key trên máy cá nhân, không đưa vào repo                                                          |
| Inbound ports                            | SSH (22) để vào máy; sau khi tạo, giới hạn source về IP công khai của bạn                                                                                       |
| Disks → OS disk                         | **Standard SSD**, khoảng **30 GiB** nếu Portal cho chọn; không chọn ephemeral OS disk                                                                     |
| Networking → Virtual network/subnet     | Tạo VNet và subnet mới do Portal đề xuất hoặc dùng VNet của nhóm đã kiểm tra; Azure không có “default VPC” như AWS                                       |
| Networking → Public IP                  | **Standard, Static, IPv4** để IP origin không đổi sau stop/start; kiểm tra phí                                                                                |
| Networking → NIC network security group | **Basic** để Portal tạo NSG, rồi kiểm tra inbound rules sau khi tạo                                                                                            |
| Management                               | Boot diagnostics theo nhu cầu; kiểm tra phí của tùy chọn bổ sung trước khi bật                                                                                   |

Chọn **Review + create → Create**. Nếu báo `RequestDisallowedByAzure`, xem lại region của **mọi tài nguyên** được tạo cùng VM. Nếu báo quota/capacity, chọn size khác trong cùng vùng hoặc chuyển sang Japan East/Korea Central và đổi các bước tiếp theo cho đúng vùng. Chỉ đánh dấu đã tạo khi Portal báo **Deployment succeeded**.

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
| **HTTP / TCP 80**                             | Bấm**Add** → **Source: Any**, **Source port ranges: `*`**, **Destination: Any**, **Service: HTTP** hoặc **Protocol: TCP** với **Destination port ranges: 80**, **Action: Allow**, **Priority** là số chưa dùng (ví dụ `310`) → **Add**. Cổng này dùng cho NGINX và health check.                                                       |
| **8080 / 443**                                | Không thêm rule 8080 vì FastAPI chỉ dùng trong Docker. Cổng 443 chỉ mở khi cấu hình HTTPS ở GĐ5.                                                                                                                                                                                                                                                                                                          |

Không mở cổng DB hoặc Docker daemon. Chỉ thay IP nguồn của rule SSH khi chắc chắn đó là IP công khai hiện tại của máy đang dùng; nhập sai sẽ khiến SSH bị chặn. [Microsoft: NSG](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview).

### 5.3. SSH từ máy cá nhân

Mở **PowerShell trên máy cá nhân** (không phải terminal trong Azure Portal). Trong lệnh mẫu dưới đây, thay `YOUR_WINDOWS_USER`, `YOUR_KEY.pem` và `203.0.113.10` bằng đường dẫn file `.pem` vừa tải và Public IP thật của VM; giữ đúng username đã chọn ở tab Basics, ví dụ `azureuser`:

```powershell
ssh -i "C:\Users\YOUR_WINDOWS_USER\Downloads\YOUR_KEY.pem" azureuser@203.0.113.10
```

Nếu SSH timeout, kiểm tra VM đang **Running**, Public IP, rule TCP 22 của NSG và IP nguồn `/32` của bạn. Nếu báo lỗi xác thực, kiểm tra username và đúng file `.pem` đã tải khi tạo VM.

## 6. Cài Docker trên VM

Các lệnh sau chạy **trong SSH trên Azure VM**. Cài Docker Engine và Compose plugin theo [hướng dẫn chính thức cho Ubuntu](https://docs.docker.com/engine/install/ubuntu/):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
```

Lệnh cuối phải in phiên bản Compose. Giữ cách dùng `sudo docker` như bên AWS; không cần thêm user vào nhóm `docker`.

## 7. Tạo Compose và NGINX

```bash
sudo mkdir -p /opt/dcs29
cd /opt/dcs29
sudo nano compose.yaml
```

Dán nội dung này sau khi thay `GHCR_IMAGE`, `IMAGE_DIGEST` và `AZURE_REGION` bằng giá trị trong [bảng thông số](THONG_SO_TRIEN_KHAI.md). `APP_REGION` phải đúng mã **Location thực của VM**. Trong `nano`: `Ctrl+O`, Enter, `Ctrl+X`.

```yaml
services:
  app:
    image: GHCR_IMAGE@sha256:IMAGE_DIGEST
    environment:
      APP_ENV: azure-standby
      APP_REGION: AZURE_REGION
    restart: unless-stopped
    expose:
      - "8080"

  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - app
```

Tạo `sudo nano nginx.conf`, dán:

```nginx
server {
    listen 80;
    server_name _;
    resolver 127.0.0.11 ipv6=off valid=5s;

    location / {
        set $app_upstream app:8080;
        proxy_pass http://$app_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
    }
}
```

Chạy `sudo docker compose config -q` rồi `sudo docker compose config --images` tại `/opt/dcs29`. Dòng image phải có tên owner thật viết chữ thường và **không còn `OWNER`**; nếu vẫn còn, sửa `compose.yaml` trước khi pull, nếu không Docker sẽ báo `invalid reference format`. App chỉ `expose` cổng 8080 trong mạng Docker; NGINX mới publish cổng 80. Không rebuild image trên Azure.

## 8. Pull image private và chạy app

GHCR package hiện private. Trong SSH trên VM, nhập username GitHub có quyền đọc package và token classic có `read:packages` **từng bước**, không lưu credential vào tài liệu hoặc ảnh. Chạy lệnh đầu, nhập username; chạy lệnh hai, dán token khi terminal hỏi (token không hiện):

```bash
read -rp 'GitHub username: ' GHCR_USER
```

```bash
read -rsp 'GHCR token: ' GHCR_TOKEN; echo
```

Sau đó chạy:

```bash
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin
unset GHCR_TOKEN GHCR_USER
cd /opt/dcs29
sudo docker compose pull
sudo docker compose up -d --pull never
sudo docker compose ps
sudo docker compose logs --tail 100 app
sudo docker compose exec nginx nginx -t
```

Không logout trước khi đã pull xong; nếu logout, lần pull tiếp theo phải login lại. `restart: unless-stopped` và Docker service đã enable giúp container lên lại khi **reboot VM**. [GitHub GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) · [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

## 9. Kiểm tra và so sánh với AWS

Trong SSH trên Azure VM:

```bash
curl -fsS http://127.0.0.1/health/ready
curl -fsS http://127.0.0.1/version
curl -fsS http://127.0.0.1/api/devices
```

Trên laptop, thay IP thật:

```powershell
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/health/ready'
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/version'
Invoke-RestMethod 'http://AZURE_PUBLIC_IP/api/devices'
Test-NetConnection AZURE_PUBLIC_IP -Port 8080
```

Mở `http://AZURE_PUBLIC_IP` để thấy dashboard. `/version` phải trả `environment=azure-standby`, `region=AZURE_REGION` theo Location VM, `commit_sha=COMMIT_SHA` đúng run GĐ2 và `dataset_sha256` đầy đủ giống AWS. Gọi `/version` và `/api/devices` ở cả hai VM trong cùng lượt. Cổng 8080 phải không truy cập được từ Internet. GĐ4 dùng HTTP; HTTPS ở GĐ5.

### Tự nghiệm thu lần triển khai này

Từ laptop, gọi `/health/ready`, `/api/status`, `/api/devices`, `/version` qua `http://AZURE_PUBLIC_IP`; các endpoint phải trả HTTP 200 và dashboard phải mở được. `Test-NetConnection AZURE_PUBLIC_IP -Port 8080` phải thất bại từ Internet; đồng thời kiểm tra NSG không mở 8080.

Gọi `/version` của AWS và Azure trong cùng lượt. Hai bên phải có cùng `commit_sha` và **toàn bộ** `dataset_sha256`, còn Azure phải trả `environment=azure-standby` và `region=AZURE_REGION` đúng Location VM. Kiểm tra image đang chạy bằng `sudo docker compose ps` và `sudo docker image inspect GHCR_IMAGE@sha256:IMAGE_DIGEST` trên VM; không suy ra digest chỉ từ Compose YAML.

Nếu `region` sai, sửa `APP_REGION` trong `/opt/dcs29/compose.yaml` thành mã Location VM rồi chạy:

```bash
cd /opt/dcs29
sudo docker compose config -q
sudo docker compose up -d --no-deps --force-recreate --pull never app
curl -fsS http://127.0.0.1/version
```

Reboot Azure VM trong Portal; sau khi VM Running, kiểm tra `sudo docker compose -f /opt/dcs29/compose.yaml ps`, `/health/ready` và `/version` qua Public IP. Hai container phải tự chạy lại. Lưu output mới của lần triển khai này, không dùng ảnh hoặc kết quả của người khác.

## 10. Chuẩn bị buổi demo và chi phí

Trước demo: **Start AWS EC2 và Azure VM**, kiểm tra cả hai URL origin; chạy health check của cả hai trước khi gây lỗi AWS. Chỉ khi Azure đang `Running` và ready mới có thể làm failover tự động. Nếu Azure đang deallocated, việc bật thủ công là **phục hồi có người thao tác**, không phải auto failover.

Sau demo: vào **VM → Stop**, đợi trạng thái **Stopped (deallocated)** nếu muốn ngừng phí compute. Disk và Public IP có thể tiếp tục tính phí; giữ Static Public IP nếu cần cùng origin cho GĐ5–GĐ6. Nếu xóa VM, kiểm tra disk, NIC, NSG, Public IP còn lại trước khi kết luận hết phí. [Microsoft: VM states/billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing) · [Microsoft: disk billing](https://learn.microsoft.com/en-us/azure/virtual-machines/disks-understand-billing).

## 11. Bằng chứng và checklist

Lưu ảnh/cấu hình **không chứa private key, token, username cá nhân**: subscription đã che bớt, region, VM size, Ubuntu x64, OS disk, VNet/subnet, NSG rules, Static Public IP, Compose `ps`, image digest, logs không secret, `/health/ready`, `/version`, `/api/devices`, dashboard, kết quả reboot, chi phí dự kiến/thực tế.

- [ ] VM ở region được policy cho phép, có Static Public IP và SSH chỉ từ IP quản trị.
- [ ] NSG chỉ mở 80 cho Internet ở GĐ4; không mở 8080.
- [ ] Docker Compose chạy `app` và `nginx` bằng đúng image digest GĐ2.
- [ ] Azure trả `azure-standby`, region thực tế và version/dataset khớp AWS.
- [ ] Reboot VM xong ứng dụng tự chạy lại.
- [ ] Trước thử failover, cả AWS và Azure đều đang chạy và healthy.

Chỉ đánh dấu GĐ4 hoàn tất khi cấu hình mạng, digest đang chạy, endpoint và reboot đã được kiểm tra trên VM của bạn. Sau đó làm [GĐ5 — domain/HTTPS](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) và [GĐ6 — Route 53 failover](GIAI_DOAN_06_ROUTE53_FAILOVER.md).
