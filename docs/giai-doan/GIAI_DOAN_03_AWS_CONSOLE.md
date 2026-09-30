# GĐ3 — Hướng dẫn thao tác AWS Console và triển khai EC2

**Kết quả cần đạt:** một origin AWS độc lập phục vụ dashboard và API qua Elastic IP, chạy đúng `GHCR_IMAGE@sha256:IMAGE_DIGEST` lấy ở GĐ2. GĐ3 chỉ dùng HTTP; domain, HTTPS và Route 53 thuộc GĐ5–GĐ6. Điền [bảng thông số](THONG_SO_TRIEN_KHAI.md) trước khi nhập các ký hiệu mẫu vào lệnh.

## 1. Cần tạo những gì?

```text
Internet → Elastic IP → Security Group (80) → EC2 Ubuntu
                                        └─ Docker Compose: NGINX (80) → FastAPI (8080 nội bộ)
```

| Tài nguyên                  | Số lượng | Giá trị gợi ý                                                    |
| ----------------------------- | ----------: | -------------------------------------------------------------------- |
| Default VPC và public subnet | Dùng sẵn | Region `AWS_REGION` bạn chọn theo quota/chi phí |
| Security Group                |           1 | SSH 22 chỉ từ IP quản trị; HTTP 80 từ Internet; không mở 8080 |
| EC2                           |           1 | Ubuntu 24.04 LTS x86_64, ví dụ`t3.small` sau khi xem giá        |
| EBS root                      |           1 | `gp3`, ví dụ 12 GiB                                              |
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

Không mở 8080. Chưa mở 443 ở GĐ3; đến GĐ5 mới thêm. Thêm tags `Project=dcs29`, `Owner=<tên nhóm>`, tạo SG và ghi `sg-id`. Vào lại **Inbound rules** để bảo đảm Console không để SSH mở cho `0.0.0.0/0`. [AWS Security Groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/changing-security-group.html).

## 5. Tạo key pair và EC2

### Key pair

**EC2 → Network & Security → Key Pairs → Create key pair**: name `dcs29-aws-primary-key`, type `RSA`, file format `.pem`. Tải và giữ file `.pem` ngoài repository. Nếu đã có key pair phù hợp và còn private key, dùng lại.

### Launch instance

**EC2 → Instances → Launch instances**, kiểm tra từng mục trước khi bấm **Launch instance**:

| Mục                       | Giá trị GĐ3                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------- |
| Name                       | `dcs29-aws-primary`                                                                 |
| AMI                        | Ubuntu Server**24.04 LTS**, Canonical, **64-bit (x86)**; ghi AMI ID thật |
| Instance type              | Ví dụ`t3.small`; chọn theo giá/quota/RAM thực tế                              |
| Key pair                   | Key ở trên                                                                          |
| Network settings → VPC    | Default VPC đã kiểm tra                                                            |
| Network settings → Subnet | Public default subnet đã kiểm tra                                                  |
| Auto-assign public IP      | **Disable**; bước 6 sẽ gắn Elastic IP                                       |
| Firewall                   | **Select existing security group** → `dcs29-aws-primary-sg`                  |
| Configure storage          | Root EBS`gp3`, ví dụ 12 GiB                                                       |
| Number of instances        | 1                                                                                     |

Thêm tags `Project=dcs29`, `Owner=<tên nhóm>` cho instance và volume khi Console cho chọn resource types. Xem **Summary** và ước tính chi phí, rồi launch. Chờ trạng thái **Running** và **2/2 status checks passed**. Ghi `instance-id`, `ami-id`, `volume-id`. Image GĐ2 là `linux/amd64` nên không chọn instance Graviton/Arm. [AWS Launch instance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html).

## 6. Cấp và gắn Elastic IP

1. **EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address**. Dùng pool mặc định của Amazon, network border group thông thường của region, thêm tags rồi **Allocate**.
2. Chọn IP vừa cấp → **Actions → Associate Elastic IP address** → Resource type **Instance** → chọn `instance-id` → **Associate**.
3. Ghi IP và `eipalloc-id`. Ở **Instances → Networking**, xác nhận public IPv4 của instance chính là IP này.

Elastic IP phải cùng network border group với EC2 và được tính phí khi còn allocate. [AWS allocate/associate EIP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/working-with-eips.html).

## 7. SSH và cài Docker trên EC2

Trên laptop Windows PowerShell, thay đường dẫn key/IP thật:

```powershell
ssh -i "C:\duong-dan-ngoai-repo\dcs29-aws-primary-key.pem" ubuntu@AWS_PUBLIC_IP
```

Ubuntu AMI dùng user `ubuntu`. Nếu SSH timeout, kiểm tra EC2 Running, EIP, route Internet Gateway, SG 22 và IP hiện tại của laptop. Tab **Connect → SSH client** của instance cũng đưa lệnh tương ứng. [AWS connect EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html).

Các lệnh tiếp theo chạy **trong SSH trên EC2**. Cài Docker Engine và Compose plugin từ repository chính thức:

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
sudo docker version
sudo docker compose version
```

Hai lệnh `version` phải thành công. Dùng `sudo docker` trong lab, không cần thêm user vào nhóm `docker`. Nếu Docker đổi repository/package, đối chiếu [hướng dẫn Docker Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

## 8. Tạo Compose và NGINX trên EC2

```bash
sudo mkdir -p /opt/dcs29
cd /opt/dcs29
sudo nano compose.yaml
```

Dán nội dung sau vào `compose.yaml`; trong `nano`, lưu bằng `Ctrl+O`, Enter, `Ctrl+X`:

```yaml
services:
  app:
    image: GHCR_IMAGE@sha256:IMAGE_DIGEST
    environment:
      APP_ENV: aws-primary
      APP_REGION: AWS_REGION
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

App chỉ dùng `expose`, không có `ports`, nên 8080 không publish ra host. Image app khóa bằng digest, không `build: .` trên EC2. Tag NGINX có thể thay đổi; lưu digest NGINX thực tế trong evidence.

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

Docker cung cấp DNS nội bộ ở `127.0.0.11`; cấu hình trên cho phép NGINX phân giải lại container `app` sau khi app được tạo lại. Kiểm tra cú pháp Compose:

```bash
sudo docker compose config -q
```

## 9. Pull image và chạy ứng dụng

Nếu GHCR package **Private**, nhập token trong SSH mà không viết token vào history/file. Nếu **Public**, bỏ qua toàn bộ block login và đi thẳng tới `sudo docker compose pull`:

```bash
read -rsp 'GHCR token: ' GHCR_TOKEN; echo
printf '%s' "$GHCR_TOKEN" | sudo docker login ghcr.io -u GHCR_OWNER --password-stdin
unset GHCR_TOKEN
```

Nếu package public, bỏ qua login. Ở `/opt/dcs29`:

```bash
sudo docker compose pull
sudo docker compose up -d --pull never
sudo docker compose ps
sudo docker compose logs --tail 100 app
sudo docker compose exec nginx nginx -t
```

Sau khi pull private image, có thể chạy `sudo docker logout ghcr.io` để xóa login khỏi Docker config của root; lần pull mới cần login lại. `--pull never` buộc `up` dùng image đã tải. [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

## 10. Kiểm tra từ EC2 và laptop

Trên **EC2**:

```bash
curl -fsS http://127.0.0.1/health/ready
curl -fsS http://127.0.0.1/version
curl -fsS http://127.0.0.1/api/devices
sudo docker image inspect GHCR_IMAGE@sha256:IMAGE_DIGEST --format '{{json .RepoDigests}}'
sudo docker image inspect nginx:stable-alpine --format '{{json .RepoDigests}}'
```

Trong **PowerShell laptop**, thay Elastic IP thật:

```powershell
Invoke-RestMethod "http://AWS_PUBLIC_IP/health/ready"
Invoke-RestMethod "http://AWS_PUBLIC_IP/version"
Invoke-RestMethod "http://AWS_PUBLIC_IP/api/devices"
Test-NetConnection AWS_PUBLIC_IP -Port 8080
```

Mở `http://AWS_PUBLIC_IP` trên browser để thấy dashboard. `/version` phải có `environment=aws-primary`, `region=AWS_REGION`, commit và `dataset_sha256` khớp GĐ2. Cổng 8080 từ Internet phải không kết nối được; kiểm tra thêm screenshot SG không có rule 8080. GĐ3 dùng HTTP; HTTPS/certificate thuộc GĐ5.

### Ghi mốc AWS để đối chiếu Azure

Lưu **toàn bộ** response `/version` của EC2, đặc biệt `version`, `commit_sha`, `dataset_sha256`, `environment` và `region`. Ở GĐ4 gọi `/version` trên cả hai VM trong cùng lượt; `commit_sha` và `dataset_sha256` phải trùng, còn `environment` và `region` khác theo cloud. Không so sánh chỉ 12 ký tự hash trên giao diện.

## 11. Thử khởi động lại và xử lý lỗi

1. Trong EC2, thử tạo lại riêng app: `sudo docker compose up -d --no-deps --force-recreate --pull never app`. Sau vài giây, gọi lại `curl -fsS http://127.0.0.1/health/ready` để xác nhận NGINX vẫn tìm được app.
2. Vào **EC2 Console → Instances → chọn máy → Instance state → Reboot instance**. Đợi máy lên, SSH lại, chạy `sudo docker compose -f /opt/dcs29/compose.yaml ps` rồi kiểm tra `/version` qua Elastic IP. Hai container phải tự chạy nhờ `restart: unless-stopped` và Docker service đã enable.
3. Nếu HTTP 502: xem `sudo docker compose logs --tail 100 nginx app`, `sudo docker compose ps`, `sudo docker compose exec nginx nginx -t`.
4. Nếu app `unhealthy`: xem app logs, kiểm tra bộ nhớ/disk với `free -h` và `df -h`, gọi trực tiếp trong container bằng `sudo docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health/ready').status)"`.
5. Nếu pull GHCR bị `denied`: xác nhận token classic có `read:packages`, quyền của GitHub user trên package, SSO nếu tổ chức yêu cầu, và digest đúng GĐ2.
6. Nếu laptop không mở được cổng 80: kiểm tra EIP đã associate, route Internet Gateway, SG 80, trạng thái EC2, `docker compose ps` và NGINX logs.

AWS phải phục vụ được khi Azure chưa tồn tại hoặc đang không truy cập được. Điều này chứng minh origin độc lập, chưa phải failover.

## 12. Evidence, chi phí và rollback

Lưu bằng chứng **không chứa secret**: screenshot region/EC2/AMI/type/VPC/subnet/EIP/SG/volume; output Compose, digest, `/health/ready`, `/version`, `/api/devices`; kết quả reboot và recreate; giờ UTC bắt đầu/kết thúc; CPU/RAM/disk và chi phí thực tế. Ghi rõ instance type, số giờ EC2 bật, dung lượng EBS, thời gian giữ Elastic IP.

Khi cần đổi phiên bản, sửa **digest app** trong `compose.yaml` sang digest đã nghiệm thu, login GHCR nếu private, `sudo docker compose pull app`, `sudo docker compose up -d --pull never`; rollback bằng cách đặt lại digest trước đó và chạy lại hai lệnh. Không dùng tag `latest`. Nếu NGINX lỗi, sửa `nginx.conf`, chạy `sudo docker compose exec nginx nginx -t`, sau đó `sudo docker compose restart nginx`.

Nếu tạm nghỉ, **Stop instance** để dừng phí compute; EBS và Elastic IP/public IPv4 vẫn tính phí. Khi bật lại kiểm tra IP, Docker và `/version`. Nếu hủy hẳn lab, lưu evidence rồi terminate EC2, release Elastic IP, kiểm tra EBS còn sót; chỉ xóa SG/key/budget khi chắc chắn các giai đoạn sau không dùng. [AWS stop/start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html) · [AWS Elastic IP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/working-with-eips.html).

## 13. Checklist nghiệm thu GĐ3

- [ ] Region, default VPC, public subnet và route Internet Gateway đã được xác nhận.
- [ ] SG chỉ mở 22 từ IP quản trị và 80 từ Internet; 8080/443 chưa mở.
- [ ] EC2 Ubuntu x86_64, EBS và Elastic IP có IDs/tags được ghi lại.
- [ ] Docker Engine + Compose chạy; `app` và `nginx` hoạt động.
- [ ] App chạy đúng GHCR digest GĐ2; `/version` trả `aws-primary`, đúng region/commit/dataset.
- [ ] Laptop mở được dashboard/API qua Elastic IP cổng 80, không truy cập được 8080.
- [ ] Recreate app và reboot EC2 xong endpoint hoạt động lại.
- [ ] Evidence/chi phí/rollback được lưu, không lộ credential.

Sau khi đạt checklist, dùng **cùng digest** cho [GĐ4 — Azure Ubuntu VM](GIAI_DOAN_04_AZURE.md). Domain, HTTPS và Route 53 làm ở GĐ5–GĐ6.
