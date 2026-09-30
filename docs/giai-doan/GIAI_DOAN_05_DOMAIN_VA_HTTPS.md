# Giai đoạn 5 — Domain chung và HTTPS trên AWS EC2 / Azure VM

**Đầu vào:** GĐ3/GĐ4 đã pass HTTP trên hai origin. Điền `YOUR_DOMAIN`, `AWS_PUBLIC_IP`, `AZURE_PUBLIC_IP`, `AWS_REGION`, `AZURE_REGION`, `CERT_EMAIL` trong [bảng thông số](THONG_SO_TRIEN_KHAI.md). Domain phải thuộc quyền quản lý của bạn; thay mọi ký hiệu mẫu trước khi nhập vào Console, NGINX hoặc chạy lệnh. `aws-origin.YOUR_DOMAIN`, `azure-origin.YOUR_DOMAIN`, `app.YOUR_DOMAIN` là ba hostname nằm trong **cùng một domain**, không phải ba domain phải mua.

**Mục tiêu:** cả hai VM có HTTPS hợp lệ cho hostname origin riêng và hostname chung `app.YOUR_DOMAIN` trước khi GĐ6 tạo DNS failover. AWS dùng IAM role cho Certbot DNS-01; Azure dùng credential DNS giới hạn quyền, lưu ngoài repo. Không chép certificate/private key giữa hai cloud.

Nếu tạm nghỉ giữa GĐ5/GĐ6, có thể stop hai VM; khi bật lại, xác nhận Elastic IP/Static Public IP, container và certificate còn hoạt động. VM tắt thì cả ứng dụng lẫn renewal không chạy. [AWS EC2 lifecycle](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html) · [Azure VM billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

## 1. Ta sẽ làm gì?

```text
aws-origin.YOUR_DOMAIN   → AWS_PUBLIC_IP   → NGINX HTTPS → app:8080
azure-origin.YOUR_DOMAIN → AZURE_PUBLIC_IP  → NGINX HTTPS → app:8080

app.YOUR_DOMAIN: certificate hợp lệ trên CẢ HAI VM
                kiểm tra bằng curl --resolve trước khi tạo DNS failover
```

Route 53 chỉ trả lời DNS. Khi đổi đích từ AWS sang Azure, trình duyệt vẫn gửi TLS SNI và HTTP Host là `app.YOUR_DOMAIN`. Do đó certificate trên mỗi VM phải chứa hostname chung này.

Quy trình chính dùng **Certbot + ACME DNS-01 + plugin Route 53**. Certbot chạy trên Ubuntu host, tự thêm/xóa TXT xác thực ở Route 53; NGINX chạy trong Docker đọc certificate từ host. AWS dùng IAM role; Azure dùng IAM user riêng có quyền DNS giới hạn cho lab. Hai VM cấp certificate riêng, không sao chép private key giữa cloud. Không cần ALB, Azure Container Apps hoặc certificate của các dịch vụ đó.

**Thứ tự:** tạo hosted zone → đợi domain được duyệt → đổi bốn NS tại nhà đăng ký tên miền → kiểm tra NS công khai → tạo hai A record origin → mở 443 → quyền DNS → certificate AWS → NGINX AWS → lặp lại Azure → renewal → nghiệm thu. Chưa tạo record `app` hoặc health check trong GD5. Browser sẽ kiểm tra hai hostname origin; hostname chung được kiểm tra bằng `curl --resolve`.

### Nơi thao tác và cách đọc lệnh

| Nhãn | Nơi làm | Phạm vi |
|---|---|---|
| AWS CONSOLE | Trình duyệt laptop, AWS Console | Route 53/IAM và tài nguyên EC2 |
| AZURE PORTAL | Trình duyệt laptop, Azure Portal | VM, Public IP và NSG Azure |
| NHÀ ĐĂNG KÝ | Trình duyệt laptop, trang quản trị domain của bạn | Đổi nameserver |
| CHỈ EC2 | SSH Ubuntu EC2 `AWS_PUBLIC_IP` | Chạy một lần trên EC2 |
| CHỈ AZURE | SSH Ubuntu Azure `AZURE_PUBLIC_IP` | Chạy một lần trên Azure |
| CẢ HAI MÁY | Hai phiên SSH trên | Làm trên EC2 rồi lặp lại cùng lệnh trên Azure |
| LAPTOP | PowerShell Windows, ngoài SSH | Kiểm tra DNS/HTTP/HTTPS của cả hai cloud |

Lệnh giống nhau được ghi một lần với nhãn **CẢ HAI MÁY**. Lệnh/cấu hình khác nhau có block riêng cho EC2 và Azure. Chạy theo thứ tự từ trên xuống, dừng nếu có lỗi. Nội dung `nginx`, `yaml`, `ini` hoặc `sh` để dán vào editor/file được chỉ định, không phải chạy trực tiếp trong shell. Với lệnh Bash có dấu `\` ở cuối dòng (Certbot, OpenSSL), thay ký hiệu mẫu rồi dán **cả lệnh từ dòng đầu tới dòng cuối** trong cùng phiên SSH; Bash sẽ chờ dòng tiếp theo cho đến khi gặp dòng không có `\`. Lệnh `sudo certbot certificates` và `date -u` là lệnh kiểm tra riêng, chạy sau khi lệnh trước hoàn tất.

Trước khi kiểm tra endpoint, cả EC2 và Azure VM phải đang Running và container đã chạy. IP vẫn phân giải DNS được khi VM tắt, nhưng curl sẽ không kết nối được.

## 2. Chuẩn bị domain và địa chỉ origin

Bạn cần một domain đã đăng ký và quyền thay đổi nameserver tại nhà đăng ký. Chưa cần có sẵn subdomain: ở mục 4 bạn sẽ tạo hai A record `aws-origin`/`azure-origin`, ở GĐ6 tạo hai record failover cùng tên `app`. Nếu domain đang có website/email, sao chép các record đang dùng sang Route 53 trước khi đổi nameserver; kiểm tra DNSSEC/DS tại nhà đăng ký.

### 2.1. Các tên ta sẽ dùng

| Tên                                 | Dùng để làm gì?                                                     | Khi nào tạo DNS?                                                             |
| ------------------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `YOUR_DOMAIN`              | Domain bạn đã đăng ký, đồng thời là tên hosted zone           | Mục 3 tạo zone; chưa cần A record cho domain gốc                          |
| `aws-origin.YOUR_DOMAIN`   | Địa chỉ riêng của ứng dụng trên AWS                              | Mục 4 tạo A record`aws-origin`                                             |
| `azure-origin.YOUR_DOMAIN` | Địa chỉ riêng của ứng dụng trên Azure                            | Mục 4 tạo A record`azure-origin`                                           |
| `app.YOUR_DOMAIN`          | Địa chỉ chung của người dùng, sẽ tự đổi cloud khi có sự cố | GĐ5 cấp certificate và kiểm tra ép IP; GĐ6 mới tạo DNS failover`app` |

Ví dụ: khi thêm A record tên **`aws-origin`** vào hosted zone **`YOUR_DOMAIN`**, bạn vừa tạo subdomain **`aws-origin.YOUR_DOMAIN`**. Không cần tạo thư mục, mua hosting hoặc xin subdomain từ nhà đăng ký tên miền.

### 2.2. Trước khi mở Route 53, lấy hai IP của VM

**Lấy AWS Elastic IP:**

1. Mở **AWS Console → EC2**, chọn đúng region `AWS_REGION` của EC2 đã tạo ở GĐ3.
2. Vào **Instances → chọn EC2 đang chạy ứng dụng → Networking**; xem **Elastic IP addresses**. Có thể vào **Network & Security → Elastic IPs** và đối chiếu **Associated instance ID** với EC2 này.
3. Sao chép Elastic IPv4 đang gắn với EC2. Không lấy private IPv4, không lấy tên Public IPv4 DNS. Nếu chưa gắn Elastic IP, hoàn thành mục 6 của [GĐ3](GIAI_DOAN_03_AWS_CONSOLE.md#6-cấp-và-gắn-elastic-ip) trước.

**Lấy Azure Static Public IP:**

1. Mở **Azure Portal → Virtual machines → chọn VM GĐ4 → Overview**.
2. Sao chép **Public IP address**, rồi mở resource Public IP qua liên kết đó hoặc qua **Networking → Network settings → NIC → IP configurations → Public IP**.
3. Xác nhận **IP address** đúng và **IP assignment / Allocation method = Static**. Nếu là Dynamic, chưa dùng làm origin DNS ổn định; hoàn thành kiểm tra Public IP của GĐ4 trước.

Lưu một ghi chú cá nhân như sau; chỉ điền những giá trị hiện đã có:

```text
Domain / Hosted zone name: YOUR_DOMAIN
Shared hostname: app.YOUR_DOMAIN
AWS origin hostname: aws-origin.YOUR_DOMAIN
Azure origin hostname: azure-origin.YOUR_DOMAIN
AWS Public IP: AWS_PUBLIC_IP (đối chiếu Elastic IP)
Azure Public IP: AZURE_PUBLIC_IP (đối chiếu Static)
Hosted zone ID: <để trống; lấy sau khi làm mục 3.1>
Email nhận thông báo certificate: CERT_EMAIL
Người kiểm tra renewal: <tên thành viên phụ trách>
```

**Không cần tự nghĩ giá trị Hosted zone ID hoặc nameserver.** AWS sẽ tạo chúng khi bạn tạo hosted zone. Email dùng trong lệnh Certbot phải thay bằng email thật đang nhận thư; không dùng `CERT_EMAIL` nếu bạn chưa tạo mailbox đó.

### 2.3. Thứ tự làm từ đầu

1. Hoàn thành GĐ3/GĐ4, lấy `AWS_PUBLIC_IP` là Elastic IP và `AZURE_PUBLIC_IP` là Static Public IP.
2. Tạo **một** public hosted zone cho `YOUR_DOMAIN` ở Route 53; ghi `HOSTED_ZONE_ID` và **bốn NS mới của zone này**.
3. Tại nhà đăng ký domain của bạn, đổi nameserver sang đúng bốn NS vừa lấy. Chờ truy vấn NS công khai khớp rồi mới tiếp tục.
4. Tạo hai A record origin tới hai IP thật; xác nhận HTTP qua từng hostname.
5. Mở 443, cấp quyền DNS-01, cấp certificate riêng trên AWS rồi Azure, cấu hình NGINX và kiểm tra HTTPS/renewal.

Không dùng IP, NS, hosted zone ID hoặc certificate của người triển khai khác.

## 3. AWS Console — tạo public hosted zone và nối domain

### 3.1. Tạo hosted zone và lưu NS

1. AWS Console → **Route 53 → Hosted zones**. Nếu đã có **Public hosted zone** đúng `YOUR_DOMAIN` mà bạn sở hữu, mở nó và kiểm tra; không tạo zone trùng.
2. Nếu chưa có, chọn **Create hosted zone**: Domain name = domain thật thay cho `YOUR_DOMAIN` (không có `https://`), Type = **Public hosted zone**, description/tag tùy nhóm.
3. Sau khi tạo, ghi **Hosted zone ID** thành `HOSTED_ZONE_ID`. Trong record **NS**, sao chép đủ bốn nameserver vào `ROUTE53_NS_1` … `ROUTE53_NS_4`. Giữ record NS/SOA mặc định. Mỗi hosted zone có bộ NS riêng; tuyệt đối không chép NS từ ví dụ hoặc lần tạo cũ.

| Ô tại nhà đăng ký | Giá trị phải lấy từ zone của bạn |
|---|---|
| Nameserver 1 | `ROUTE53_NS_1` |
| Nameserver 2 | `ROUTE53_NS_2` |
| Nameserver 3 | `ROUTE53_NS_3` |
| Nameserver 4 | `ROUTE53_NS_4` |

Nếu Route 53 hiển thị dấu chấm cuối hostname, nhà đăng ký thường chấp nhận tên không có dấu chấm đó. Tạo zone chưa tự đổi delegation tại nhà đăng ký.

### 3.2. Đổi nameserver tại nhà đăng ký domain

Chỉ bắt đầu khi domain đã hoạt động và bạn có quyền sửa NS. Mở trang quản lý domain tại **nhà đăng ký của bạn** → chọn domain thật → **Nameservers/Name Server** → dùng nameserver tùy chỉnh. Nhập đủ bốn `ROUTE53_NS_1` … `ROUTE53_NS_4` từ chính hosted zone rồi lưu; mở lại trang để xác nhận. Tên nút có thể khác theo nhà đăng ký.

Nếu domain đang có website/email, chuyển đầy đủ A/AAAA/CNAME/MX/TXT cần giữ sang hosted zone trước khi đổi NS. Nếu DNSSEC đang bật, cập nhật hoặc gỡ DS cũ theo hướng dẫn nhà đăng ký để tránh `SERVFAIL`. Không tạo origin record tại nhà đăng ký sau khi đã giao DNS cho Route 53.

### 3.3. Kiểm tra DNS — chỉ sau khi đã lưu bốn NS tại nhà đăng ký tên miền

Điều kiện: mục 3.2 đã hoàn tất và mở lại trang nhà đăng ký tên miền thấy đủ bốn NS AWS đã lưu. Bây giờ mới chạy trên **PowerShell laptop**:

```powershell
Resolve-DnsName YOUR_DOMAIN -Type NS -Server 1.1.1.1
```

Kết quả phải có đủ `ROUTE53_NS_1`, `ROUTE53_NS_2`, `ROUTE53_NS_3` và `ROUTE53_NS_4`; thứ tự và dấu chấm cuối có thể khác. Nếu còn NS cũ hoặc truy vấn lỗi, chưa nghiệm thu DNS: kiểm tra domain đã kích hoạt, bốn NS tại nhà đăng ký tên miền đã lưu đúng và đợi cache/propagation cập nhật rồi thử lại. Không tạo lại hosted zone để xử lý cache. **Chỉ chuyển sang mục 4 khi bộ NS khớp.** Tạo zone trên AWS chưa chứng minh domain đã nối với Route 53. Tham khảo [AWS: nối domain với Route 53](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-configuring-new-domain.html).

## 4. AWS Console — tạo hai A record origin

**Điều kiện bắt đầu:** domain đã kích hoạt, nameserver đã đổi tại nhà đăng ký tên miền và kiểm tra NS ở mục 3.3 đã khớp Route 53.

Trong **Route 53 → Hosted zones → YOUR_DOMAIN → Create record**, tạo lần lượt hai record dưới đây. Nếu giao diện dùng wizard, chọn **Simple routing** rồi **Define simple record**; nếu dùng form thường, điền trực tiếp các trường. Tạo record AWS trước, bấm **Create records**, rồi lặp lại cho Azure:

| Trường       | AWS                         | Azure                       |
| -------------- | --------------------------- | --------------------------- |
| Record name    | `aws-origin`              | `azure-origin`            |
| Record type    | **A — IPv4 address** | **A — IPv4 address** |
| Alias          | **Off**               | **Off**               |
| Value          | **`AWS_PUBLIC_IP`**  | **`AZURE_PUBLIC_IP`** |
| TTL            | `60` giây cho lab        | `60` giây                |
| Routing policy | **Simple routing**    | **Simple routing**    |

Bấm **Create records**. Ở ô **Record name**, chỉ nhập `aws-origin` hoặc `azure-origin`; giao diện gắn phần đuôi `.YOUR_DOMAIN`. Kiểm tra tên đầy đủ trước khi lưu để tránh bị lặp domain. Ô **Value** dùng IP thật đã lấy ở mục 2.2, không nhập URL, port hoặc private IP. Sau khi lưu, danh sách phải có `aws-origin.YOUR_DOMAIN` và `azure-origin.YOUR_DOMAIN`: hai subdomain đã được tạo. Chưa thêm AAAA khi chưa triển khai IPv6.

**LAPTOP — PowerShell Windows:** chạy các lệnh dưới, không chạy trong SSH:

```powershell
Resolve-DnsName aws-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
Resolve-DnsName azure-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
curl.exe --fail-with-body http://aws-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body http://azure-origin.YOUR_DOMAIN/version
```

Mỗi tên phải trả đúng IP và environment. Nếu HTTP qua IP được nhưng qua tên không được, kiểm tra delegation/record/cache trước khi tiếp tục. GĐ5 không tạo CNAME simple cho `app`, nên GĐ6 không cần xóa record tạm do hướng dẫn này tạo.

## 5. Mở cổng HTTPS ở hai cloud

### 5.1. AWS Console

1. **EC2 → Instances → chọn EC2 GĐ3 → Security → mở Security group đang gắn với máy**.
2. **Inbound rules → Edit inbound rules → Add rule**.
3. Type **HTTPS**, protocol **TCP**, port **443**, source **Anywhere-IPv4 (`0.0.0.0/0`)**, description `dcs29 HTTPS` → **Save rules**.
4. Giữ cổng 80 để redirect HTTP; giữ SSH 22 chỉ từ IP quản trị. Không mở 8080.

### 5.2. Azure Portal

1. **Virtual machines → VM GĐ4 → Networking → Network settings** (hoặc Networking).
2. Mở NSG của NIC → **Settings → Inbound security rules → Add**.

| Trường                | Giá trị                                                                      |
| ----------------------- | ------------------------------------------------------------------------------ |
| Source                  | **Any**                                                                  |
| Source port ranges      | `*`                                                                          |
| Destination             | **Any**                                                                  |
| Service                 | **Custom** nếu không có HTTPS                                         |
| Destination port ranges | `443`                                                                        |
| Protocol                | **TCP**                                                                  |
| Action                  | **Allow**                                                                |
| Priority                | `310` nếu chưa dùng; chọn số trống đứng trước rule Deny liên quan |
| Name                    | `Allow-HTTPS-443`                                                            |

3. Bấm **Add**. Nếu subnet cũng gắn NSG, kiểm tra cả NSG subnet: traffic phải được phép qua cả hai. Giữ 80 và SSH giới hạn IP; không mở 8080.
4. Mở resource Public IP của VM, xác nhận **Static** và đúng IP của A record.

### 5.3. CẢ HAI MÁY — kiểm tra UFW trong SSH

Trên EC2 rồi Azure, chạy `sudo ufw status`. Nếu **inactive**, không chạy thêm lệnh UFW và không cần bật UFW ở bước này. Chỉ chạy lệnh mở 443 khi UFW đang **active**:

```bash
sudo ufw status
# Chỉ chạy khi UFW đang active:
sudo ufw allow 443/tcp
```

Không bật UFW mới trong bước này khi chưa chuẩn bị rule SSH. Mở firewall chưa đủ để 443 hoạt động: phải cấu hình NGINX và publish Docker port ở mục 8. Tham khảo [Microsoft: quản lý rule NSG](https://learn.microsoft.com/en-us/azure/virtual-network/manage-network-security-group).

## 6. AWS Console — quyền Route 53 cho Certbot

### 6.1. AWS CONSOLE — tạo policy một lần, dùng cho cả hai máy

**IAM → Policies → Create policy → JSON**. Dán mẫu sau, thay `HOSTED_ZONE_ID` và `YOUR_DOMAIN` bằng giá trị của bạn trước khi lưu:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "route53:ListHostedZones",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "route53:GetChange",
      "Resource": "arn:aws:route53:::change/*"
    },
    {
      "Effect": "Allow",
      "Action": "route53:ChangeResourceRecordSets",
      "Resource": "arn:aws:route53:::hostedzone/HOSTED_ZONE_ID",
      "Condition": {
        "ForAllValues:StringEquals": {
          "route53:ChangeResourceRecordSetsRecordTypes": ["TXT"],
          "route53:ChangeResourceRecordSetsNormalizedRecordNames": [
            "_acme-challenge.app.YOUR_DOMAIN",
            "_acme-challenge.aws-origin.YOUR_DOMAIN",
            "_acme-challenge.azure-origin.YOUR_DOMAIN"
          ]
        }
      }
    }
  ]
}
```

Bấm **Next**, tên `dcs29-certbot-dns` → **Create policy**. Policy cho phép liệt kê zone/đọc trạng thái thay đổi và sửa đúng các TXT ACME trong zone đã chọn; không cho sửa A/CNAME failover.

### 6.2. AWS CONSOLE — gắn IAM role CHỈ cho EC2

1. **IAM → Roles → Create role** → trusted entity **AWS service** → use case **EC2** → **Next**.
2. Chọn policy `dcs29-certbot-dns` → **Next** → tên `dcs29-ec2-certbot` → **Create role**.
3. **EC2 → Instances → chọn máy → Actions → Security → Modify IAM role** → chọn role vừa tạo → **Update IAM role**.
4. Nếu EC2 đã có role đang dùng, thêm policy vào role hiện hữu thay vì thay role và làm mất quyền cũ.

Certbot chạy trên host EC2 sẽ lấy credential tạm từ instance role. Không tạo access key trên EC2. Xem [AWS: gắn IAM role vào EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/attach-iam-role.html).

### 6.3. Credential CHỈ cho Azure — tạo ở AWS, lưu trên Azure

Azure VM không tự nhận EC2 IAM role. Với quy trình lab này, tạo IAM user dành riêng cho renewal Azure; không dùng root key hay key quản trị cá nhân.

**A. AWS CONSOLE — tạo IAM user/key cho Azure (không làm ở Azure Portal):**

1. **IAM → Users → Create user** → tên `dcs29-azure-certbot`; không chọn cấp quyền đăng nhập AWS Console.
2. **Permissions → Attach policies directly** → chọn `dcs29-certbot-dns` → **Next → Create user**.
3. Mở user → **Security credentials → Access keys → Create access key** → chọn **Application running outside AWS** nếu có; đọc xác nhận, hoặc **Other** theo giao diện → **Next → Create access key**.
4. Lưu key ở nơi quản lý secret ngoài repo. Secret access key chỉ hiện lúc tạo; không chụp màn hình chứa key và không gửi key trong chat. Xem [AWS: quản lý access key](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-keys-admin-managed.html).

**B. CHỈ AZURE — SSH vào Azure VM, lưu cả Access key ID và Secret access key:**

Không tạo file credential này trên EC2 vì EC2 đã dùng IAM role. Chạy từ bất kỳ thư mục nào trong SSH Azure:

```bash
sudo install -d -m 700 /root/.aws
sudo nano /root/.aws/credentials
```

Nhập trực tiếp trong editor, thay placeholder bằng key thật:

```ini
[default]
aws_access_key_id = YOUR_AZURE_CERTBOT_ACCESS_KEY_ID
aws_secret_access_key = YOUR_AZURE_CERTBOT_SECRET_ACCESS_KEY
```

Lưu `Ctrl+O`, Enter, thoát `Ctrl+X`, rồi:

```bash
sudo chmod 600 /root/.aws/credentials
```

Không chạy `cat` file để lấy evidence. Giữ file trên host cho renewal; không mount credential vào container app/NGINX. Sau lab, khi không còn cần renewal, deactivate rồi xóa key. Muốn bỏ long-term key ở môi trường lâu dài, thiết kế credential tạm như IAM Roles Anywhere riêng; không nằm trong bước lab này.

## 7. Cấp certificate — làm AWS trước, Azure sau

### 7.1. CẢ HAI MÁY — cài Certbot, cùng lệnh

Chạy đủ các lệnh dưới trên **SSH EC2 trước**, rồi lặp lại trên **SSH Azure**. Không chạy trên PowerShell laptop hoặc trong container. Có thể đứng ở bất kỳ thư mục nào:

```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-dns-route53
sudo certbot --version
sudo certbot plugins
```

Danh sách plugin phải có `dns-route53`. Hướng dẫn dùng package Ubuntu; không trộn thêm bản snap/pip nếu đã cài Certbot theo cách khác. Nếu plugin thiếu, kiểm tra package/source trước khi cấp certificate.

### 7.2. CHỈ EC2 — cấp certificate dcs29-aws

Thay `YOUR_DOMAIN` bằng domain thật và `CERT_EMAIL` bằng email nhận thư trước khi chạy lệnh:

```bash
sudo certbot certonly --dns-route53 \
  --cert-name dcs29-aws \
  -d app.YOUR_DOMAIN \
  -d aws-origin.YOUR_DOMAIN \
  --email CERT_EMAIL --agree-tos --non-interactive
sudo certbot certificates
```

Kỳ vọng certificate có hai SAN đúng và đường dẫn:

```text
/etc/letsencrypt/live/dcs29-aws/fullchain.pem
/etc/letsencrypt/live/dcs29-aws/privkey.pem
```

### 7.3. CHỈ AZURE — cấp certificate dcs29-azure

Đợi cấp certificate EC2 xong. Trong **SSH Azure**, đảm bảo file credential mục 6.3 đã tạo; thay `YOUR_DOMAIN` và `CERT_EMAIL` bằng giá trị thật rồi chạy:

```bash
sudo certbot certonly --dns-route53 \
  --cert-name dcs29-azure \
  -d app.YOUR_DOMAIN \
  -d azure-origin.YOUR_DOMAIN \
  --email CERT_EMAIL --agree-tos --non-interactive
sudo certbot certificates
```

Đường dẫn tương ứng là `/etc/letsencrypt/live/dcs29-azure/`. Hai certificate có cùng SAN `app...`, nhưng hostname origin, serial và private key có thể khác nhau; đó là kết quả mong đợi.

Certbot tự xử lý TXT DNS-01. Cấp lần lượt trên hai VM, kể cả khi renewal thử nghiệm, để tránh cùng sửa TXT `_acme-challenge.app...`. Không cần record A của `app` để cấp certificate DNS-01. Nếu CAA đang hạn chế issuer, chủ domain phải cho phép Let's Encrypt. Plugin cần outbound tới AWS API và ACME. [Plugin Route 53](https://certbot-dns-route53.readthedocs.io/en/stable/) mô tả cơ chế credential và DNS challenge.

**Dừng nếu cấp certificate thất bại:** chưa sửa NGINX tham chiếu tới file không tồn tại. Không cấp certificate lặp nhiều lần khi chưa sửa lỗi để tránh rate limit.

## 8. NGINX / Docker Compose — cấu hình HTTPS

Thứ tự cụ thể: **8.1 backup cả hai máy → 8.2 sửa Compose cả hai máy → 8.3 cấu hình EC2 và chạy 8.5 trên EC2 → 8.4 cấu hình Azure và chạy 8.5 trên Azure**. Lệnh 8.1/8.2/8.5 giống nhau nên chỉ ghi một bản. Không thay toàn bộ Compose vì sẽ mất cấu hình app GĐ3/GĐ4.

### 8.1. CẢ HAI MÁY — backup, cùng lệnh

Trong SSH EC2, chạy các lệnh từ trên xuống trong cùng phiên SSH để biến `BACKUP_DIR` còn giá trị. Xong ghi đường dẫn backup EC2, rồi lặp lại trên SSH Azure và ghi đường dẫn backup Azure riêng:

```bash
cd /opt/dcs29
BACKUP_DIR="/opt/dcs29/backup-gd5-$(date -u +%Y%m%dT%H%M%SZ)"
sudo mkdir -p "$BACKUP_DIR"
sudo cp compose.yaml nginx.conf "$BACKUP_DIR/"
printf 'Backup: %s\n' "$BACKUP_DIR"
```

Ghi đường dẫn này để rollback. Backup Compose có thể chứa cấu hình nhạy cảm; không đưa nguyên file lên Git nếu có secret.

### 8.2. CẢ HAI MÁY — sửa Compose, cùng nội dung

Làm trên SSH EC2 rồi SSH Azure. Lệnh mở file dùng đường dẫn tuyệt đối nên không cần đứng ở thư mục cụ thể. Bước này chỉ sửa file, chưa recreate container:

```bash
sudo nano /opt/dcs29/compose.yaml
```

Trong **service `nginx` hiện có**, sửa `ports` và `volumes` thành:

```yaml
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
```

Lưu **Ctrl+O → Enter → Ctrl+X** trên từng máy. Giữ image, restart policy, depends_on và service app như cũ. Mount cả `/etc/letsencrypt` vì file trong `live/` là symlink tới `archive/`; mount chỉ một file có thể làm renewal không cập nhật đúng. Không đưa certificate/private key vào Docker image hay repo.

### 8.3. CHỈ EC2 — cấu hình NGINX AWS

Trong **SSH EC2**, chạy `sudo nano /opt/dcs29/nginx.conf`, xóa nội dung cũ và dán cấu hình dưới vào nano. Thay `YOUR_DOMAIN` bằng domain thật trong mọi `server_name` và đường dẫn certificate trước khi lưu. Giai đoạn kiểm tra ban đầu giữ HTTP proxy; sẽ bật redirect ở mục 9.

```nginx
server {
    listen 80;
    server_name app.YOUR_DOMAIN aws-origin.YOUR_DOMAIN;
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

server {
    listen 443 ssl;
    server_name app.YOUR_DOMAIN aws-origin.YOUR_DOMAIN;
    ssl_certificate /etc/letsencrypt/live/dcs29-aws/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dcs29-aws/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
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

Lưu **Ctrl+O → Enter → Ctrl+X** rồi thực hiện mục **8.5 trên EC2**. Sau khi EC2 pass, chuyển sang mục 8.4 cho Azure.

### 8.4. CHỈ AZURE — cấu hình NGINX Azure đầy đủ để copy

Chạy trên **SSH Azure VM**, không chạy trên EC2 hoặc PowerShell laptop. Certificate `dcs29-azure` ở mục 7.3 phải đã cấp thành công, và Compose đã thêm port/mount ở mục 8.2.

Mở file:

```bash
sudo nano /opt/dcs29/nginx.conf
```

Xóa nội dung cũ, rồi dán toàn bộ cấu hình sau. Thay `YOUR_DOMAIN` bằng domain thật trong mọi `server_name` và đường dẫn certificate trước khi lưu:

```nginx
server {
    listen 80;
    server_name app.YOUR_DOMAIN azure-origin.YOUR_DOMAIN;
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

server {
    listen 443 ssl;
    server_name app.YOUR_DOMAIN azure-origin.YOUR_DOMAIN;
    ssl_certificate /etc/letsencrypt/live/dcs29-azure/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dcs29-azure/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
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

Lưu bằng **Ctrl+O → Enter → Ctrl+X**. Đây mới là bước sửa file; tiếp tục mục 8.5 để kiểm tra cú pháp trước khi tạo lại NGINX. HTTP vẫn proxy trong lúc kiểm tra; chỉ bật redirect sau khi HTTPS pass ở mục 9.3.

### 8.5. CẢ HAI MÁY — kiểm tra và áp dụng, cùng lệnh

Chạy trên **SSH EC2 sau 8.3**, rồi chạy cùng các lệnh trên **SSH Azure sau 8.4**. Lệnh kiểm tra/ap dụng giống nhau; config của từng máy đã được chuẩn bị riêng:

```bash
cd /opt/dcs29
sudo docker compose config -q
sudo docker compose run --rm --no-deps nginx nginx -t
```

Lệnh `run` kiểm tra config với mount certificate mới mà không publish port. Nếu báo syntax/certificate error, sửa trước, chưa chạy tiếp. Khi đã pass:

```bash
sudo docker compose up -d --no-deps --force-recreate --pull never nginx
sudo docker compose ps
sudo docker compose exec -T nginx nginx -t
sudo docker compose logs --tail 50 nginx
```

Cần recreate vì đã thêm port/mount; chỉ reload không thêm được port Docker. App container không bị recreate. Có gián đoạn ngắn khi thay NGINX. `ps` phải cho thấy port 80 và 443 được publish.

## 9. Kiểm tra HTTPS rồi bật redirect

### 9.1. Kiểm tra từng origin từ PowerShell laptop

Dùng `curl.exe` để tránh alias `curl` của Windows PowerShell. Không dùng `-k` hay `--insecure`:

```powershell
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/health/ready
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/ready
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/version
```

Mở hai URL origin trên browser: có HTTPS hợp lệ, dashboard hiển thị đúng environment. `https://PUBLIC_IP` có thể báo mismatch vì certificate cấp cho tên miền, không phải IP; dùng hostname đúng.

### 9.2. LAPTOP — kiểm tra hostname chung ép tới từng VM

Chạy toàn bộ cụm dưới trong **cùng tab PowerShell laptop**, không chạy trong SSH EC2/Azure. Ba dòng đầu đặt biến, rồi mỗi lệnh curl kiểm tra một origin:

```powershell
$SharedHost = "app.YOUR_DOMAIN"
$AwsIp = "AWS_PUBLIC_IP"
$AzureIp = "AZURE_PUBLIC_IP"

# EC2 (AWS_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/version"
# Azure (AZURE_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/version"
# EC2 (AWS_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/health/ready"
# Azure (AZURE_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/health/ready"
# EC2 (AWS_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/api/devices"
# Azure (AZURE_PUBLIC_IP)
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/api/devices"
```

`--resolve` chỉ ép IP cho tiến trình curl; vẫn gửi đúng TLS SNI/Host và kiểm tra certificate. Không cần sửa hosts file. Chỉ thêm HTTP header `Host:` khi gọi HTTPS qua IP không thay được TLS SNI.

Kỳ vọng: AWS trả `aws-primary`, Azure trả `azure-standby`; `commit_sha` và **dataset SHA-256 đầy đủ** giống nhau. Lấy output mới từ cả hai VM trong cùng lượt và đối chiếu với `COMMIT_SHA` của GĐ2.

### 9.3. Bật redirect — cấu hình riêng, lệnh áp dụng chung

**Điều kiện:** HTTPS ở 9.1/9.2 đã pass. Chỉ thay block `server` cổng 80; giữ nguyên toàn bộ block `listen 443 ssl;` đã hoạt động. Không sửa Compose ở bước này.

#### A. MỖI MÁY MỘT BLOCK — sửa file trên EC2 rồi Azure

Lệnh mở editor giống nhau; chạy trong SSH của từng máy:

```bash
sudo nano /opt/dcs29/nginx.conf
```

Trong file, xóa toàn bộ block đầu tiên chứa `listen 80;`, từ `server {` tới dấu `}` đóng block đó, gồm cả `location / { ... }` bên trong. Không xóa block cổng 443 và không chỉ xóa riêng dòng `listen 80;`.

**CHỈ EC2 — thay block 80 bằng:**

```nginx
server {
    listen 80;
    server_name app.YOUR_DOMAIN aws-origin.YOUR_DOMAIN;
    return 301 https://$host$request_uri;
}
```

**CHỈ AZURE — thay block 80 bằng:**

```nginx
server {
    listen 80;
    server_name app.YOUR_DOMAIN azure-origin.YOUR_DOMAIN;
    return 301 https://$host$request_uri;
}
```

Trên từng máy, lưu **Ctrl+O → Enter → Ctrl+X**. File còn đúng hai block: 80 redirect và 443 proxy. EC2 giữ certificate `dcs29-aws`; Azure giữ `dcs29-azure`. Không dán block AWS vào Azure.

#### B. CẢ HAI MÁY — cùng lệnh kiểm tra và áp dụng

Sau khi sửa EC2, chạy bước B trên SSH EC2. Sau đó sửa Azure và lặp lại bước B trên SSH Azure. Lệnh giống nhau nên chỉ ghi một bản.

**1. Kiểm tra trước:**

```bash
cd /opt/dcs29
sudo docker compose run --rm --no-deps nginx nginx -t
```

Phải có `syntax is ok` và `test is successful`. Nếu lỗi thì dừng, sửa file trên máy đang kiểm tra; chưa chạy lệnh áp dụng.

**2. Khi kiểm tra pass, áp dụng:**

```bash
sudo docker compose up -d --no-deps --force-recreate --pull never nginx
sudo docker compose ps
```

NGINX phải Running/Up và publish 80/443. Chỉ NGINX được recreate, app giữ nguyên; có gián đoạn ngắn. Recreate giúp mount nhận file config hiện tại nếu editor đã thay file trên host.

**3. Nếu NGINX không lên**, chạy trong SSH của máy lỗi:

```bash
sudo docker compose logs --tail 50 nginx
```

Cả hai máy hoạt động rồi mới sang bước C.

#### C. Trên PowerShell laptop — xác nhận redirect

Mở tab PowerShell ở laptop, **không chạy trong SSH**. Chạy từng lệnh:

```powershell
curl.exe -I http://aws-origin.YOUR_DOMAIN/version
```

Kỳ vọng có:

```text
HTTP/1.1 301 Moved Permanently
Location: https://aws-origin.YOUR_DOMAIN/version
```

Tiếp theo:

```powershell
curl.exe -I http://azure-origin.YOUR_DOMAIN/version
```

Kỳ vọng có:

```text
HTTP/1.1 301 Moved Permanently
Location: https://azure-origin.YOUR_DOMAIN/version
```

Các header khác có thể khác mẫu; quan trọng là **301** và **Location đúng HTTPS, hostname, đường dẫn**. Lệnh `-I` chỉ đọc header, không tự đi theo redirect.

Để kiểm tra cả redirect và ứng dụng sau redirect, chạy:

```powershell
curl.exe --fail-with-body -L http://aws-origin.YOUR_DOMAIN/version
```

```powershell
curl.exe --fail-with-body -L http://azure-origin.YOUR_DOMAIN/version
```

`-L` yêu cầu curl đi theo redirect. Kết quả phải là JSON `/version`: AWS trả `aws-primary`, Azure trả `azure-standby`, version/dataset vẫn khớp và không lỗi TLS. Có thể mở hai URL HTTP origin trên browser để thấy thanh địa chỉ chuyển sang HTTPS.

**Nếu chưa đúng:**

| Kết quả                          | Việc cần kiểm tra                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------------------- |
| HTTP vẫn trả 200 thay vì 301    | Block 80 chưa thay đúng hoặc chưa recreate NGINX; kiểm tra file trên đúng VM |
| Redirect tới hostname cloud khác | Sai`server_name` hoặc `return`; dùng đúng block riêng của từng máy        |
| 301 đúng nhưng`-L` lỗi       | Kiểm tra HTTPS/443, certificate và NGINX logs; gọi lại URL HTTPS trực tiếp      |
| Redirect lặp                      | Chỉ đặt`return 301` trong block 80; block 443 phải giữ proxy tới app          |

**Hoàn thành 9.3** khi cả hai origin trả 301 đúng và các lệnh `-L` nhận JSON đúng. Giữ cổng 80 mở để redirect; từ đây dùng HTTPS để kiểm tra health. Tiếp tục **mục 10: renewal certificate**. GĐ6 sẽ dùng health check HTTPS/443 trên hai hostname origin.

## 10. Renewal tự động — lệnh chung và lịch riêng cho hai máy

Certificate có hạn; ghi ngày hết hạn thực tế từ `certbot certificates`. Certbot cần quyền DNS còn hoạt động và hook reload NGINX sau khi gia hạn.

**Quy ước:** mục ghi **CẢ HAI MÁY** thì dùng đúng cùng lệnh/nội dung trên SSH EC2 và SSH Azure; không cần đổi gì. Mục ghi **CHỈ EC2** hoặc **CHỈ AZURE** chỉ thực hiện trên máy đó. Tất cả lệnh mục 10 chạy trong SSH, không phải PowerShell laptop; có thể bắt đầu từ bất kỳ thư mục nào vì đường dẫn đã tuyệt đối hoặc hook tự `cd`.

| Bước | Chạy ở đâu? | Có khác nhau không? |
|---|---|---|
| 10.1 Tạo và thử hook | Cả hai máy | Không; nội dung giống nhau |
| 10.2 Dry-run renewal | Mỗi máy dùng lệnh riêng | Khác cert-name |
| 10.3 Bật timer | Cả hai máy | Không |
| 10.4 Chỉnh lịch | EC2 và Azure có block riêng | Khác giờ chạy |
| 10.5 Áp dụng và kiểm tra | Cả hai máy | Lệnh giống, lịch kết quả khác |

### 10.1. CẢ HAI MÁY — tạo hook reload NGINX

Làm trọn bước này trên EC2, rồi lặp lại cùng nội dung trên Azure.

**1. Tạo thư mục và mở editor:** chạy lần lượt:

```bash
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo nano /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-nginx
```

**2. Trong nano**, dán nội dung sau, không dán trực tiếp vào shell:

```sh
#!/bin/sh
set -eu
cd /opt/dcs29
/usr/bin/docker compose exec -T nginx nginx -t
/usr/bin/docker compose exec -T nginx nginx -s reload
```

Lưu bằng **Ctrl+O → Enter → Ctrl+X**.

**3. Cấp quyền chạy và thử hook:**

```bash
sudo chmod 750 /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-nginx
sudo /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-nginx
```

Phải có thông báo kiểm tra NGINX thành công và không có lỗi reload. Nếu lỗi, kiểm tra NGINX đang chạy và cấu hình trước khi tiếp tục. Hook giống nhau trên hai máy vì cả hai Compose nằm ở `/opt/dcs29`; hook không recreate app.

### 10.2. MỖI MÁY MỘT LỆNH — thử renewal

**CHỈ EC2:**

```bash
sudo certbot renew --cert-name dcs29-aws --dry-run
```

Đợi EC2 hoàn tất thành công rồi chuyển sang Azure, không chạy đồng thời vì hai máy cùng xác thực TXT của `app.YOUR_DOMAIN`.

**CHỈ AZURE:**

```bash
sudo certbot renew --cert-name dcs29-azure --dry-run
```

Kỳ vọng thông báo simulated renewal thành công. Nếu lỗi, xử lý quyền Route 53/credential trước khi nghiệm thu. Dry-run dùng certificate thử nghiệm, không thay production certificate. Nó mặc định không chạy deploy hook, nên ta đã thử hook riêng ở 10.1. [Certbot: renewal và hooks](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates).

### 10.3. CẢ HAI MÁY — bật timer

Chạy cả cụm dưới trên EC2, rồi chạy cùng cụm trên Azure:

```bash
sudo systemctl enable --now certbot.timer
systemctl list-timers --all certbot.timer
sudo systemctl status certbot.timer --no-pager
```

`status` phải báo **active (waiting)**; `list-timers` có lịch chạy tiếp theo. Nếu pager vẫn mở, nhấn `q` để thoát. Nếu báo không tìm thấy unit, kiểm tra cách cài Certbot/package; không tạo scheduler thứ hai khi chưa rõ lịch hiện có.

### 10.4. CHỈNH LỊCH RIÊNG — EC2 và Azure khác giờ

Ta đặt EC2 00:00/12:00 UTC, Azure 06:00/18:00 UTC để tránh cùng sửa TXT ACME. Hai máy chạy cùng lệnh mở editor, nhưng **dán block khác nhau** như dưới.

#### A. CHỈ EC2 — lịch 00:00 và 12:00 UTC

Trong SSH EC2:

```bash
sudo systemctl edit certbot.timer
```

Editor mở file override, thường có nhiều comment bắt đầu bằng `#`. Đặt con trỏ vào vùng trống **phía trên dòng**:

```text
### Edits below this comment will be discarded
```

Dán nguyên block này vào vùng được lưu; không thêm `#` trước các dòng:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 00,12:00:00 UTC
RandomizedDelaySec=0
```

Nếu vùng phía trên đã có override `[Timer]`, sửa block đó thay vì thêm một lịch mới trùng. Không dán dưới dòng cảnh báo hoặc vào phần nội dung mặc định đang comment ở cuối file: phần đó sẽ bị bỏ khi lưu.

Nếu editor là **nano**, lưu **Ctrl+O → Enter → Ctrl+X**. Nếu là **vim**, nhấn `i` trước khi dán, rồi `Esc`, gõ `:wq`, Enter để lưu/thoát.

#### B. CHỈ AZURE — lịch 06:00 và 18:00 UTC

Trong SSH Azure:

```bash
sudo systemctl edit certbot.timer
```

Cũng dán **phía trên** dòng `### Edits below this comment will be discarded`, nhưng dùng block Azure này:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 06,18:00:00 UTC
RandomizedDelaySec=0
```

Lưu theo editor như phần A. Không copy block 00/12 UTC của EC2 vào Azure.

**Ý nghĩa các dòng chung:** `OnCalendar=` để trống là có chủ ý, xóa lịch mặc định trước khi đặt lịch mới. `RandomizedDelaySec=0` bỏ độ trễ ngẫu nhiên để hai lịch giữ cách nhau. Các dòng cấu hình không bắt đầu bằng `#`.

### 10.5. CẢ HAI MÁY — áp dụng lịch và kiểm tra

Sau khi lưu override, chạy cùng cụm sau trên EC2 và Azure:

```bash
sudo systemctl daemon-reload
sudo systemctl restart certbot.timer
systemctl list-timers --all certbot.timer
sudo systemctl status certbot.timer --no-pager
systemctl cat certbot.timer
```

Trong `systemctl cat`, tìm phần **override.conf** để xác nhận block vừa lưu có mặt. File mặc định và override đều được hiển thị; lịch mới thay thế lịch OnCalendar cũ nhờ dòng reset trống.

| Máy | Lịch UTC đã đặt | Quy đổi giờ Việt Nam (UTC+7) |
|---|---|---|
| EC2 | 00:00 và 12:00 | 07:00 và 19:00 |
| Azure | 06:00 và 18:00 | 13:00 và 01:00 ngày tiếp theo |

`list-timers` có thể hiển thị theo timezone của máy; đối chiếu với giờ UTC trên, không yêu cầu hai máy hiện cùng giờ NEXT. Timer chỉ gọi Certbot theo lịch; Certbot quyết định certificate đã đến kỳ cần renew chưa, không phải mỗi lần chạy đều cấp lại.

Nếu thấy `Editing ... canceled: temporary file is empty`, override chưa được lưu: mở lại và dán phía trên dòng cảnh báo. Nếu timer của bản cài có thêm lịch tương đối ngoài OnCalendar, kiểm tra và loại lịch trùng trước khi nghiệm thu. Khi thử thủ công, vẫn chạy lần lượt hai máy.

VM tắt/deallocate sẽ không renew. Trước demo bật cả hai, kiểm tra certificate còn hạn và HTTPS hoạt động.

### 10.6. CẢ HAI MÁY — kiểm tra định kỳ

Mỗi tuần owner chạy trên từng máy:

```bash
sudo certbot certificates
sudo journalctl -u certbot.service --since '7 days ago' --no-pager
```

Kiểm tra ngày hết hạn và lỗi renewal, rồi gọi lại HTTPS từ laptop. Azure cần file credential root-only còn hợp lệ; nếu đổi key thì cập nhật và thử dry-run lại. EC2 cần IAM role vẫn gắn. Không coi reload thành công là bằng chứng đủ nếu endpoint HTTPS ngoài máy vẫn lỗi.

## 11. Lỗi thường gặp

| Triệu chứng                                | Cách kiểm tra/sửa                                                                                                          |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| DNS`NXDOMAIN` / IP sai                     | NS tại registrar/zone cha, zone public, A record, cache; không tạo thêm zone trùng                                       |
| DNS`SERVFAIL`                              | Delegation/DNSSEC DS cũ; kiểm tra với chủ domain/registrar                                                                |
| Certbot`AccessDenied`                      | Zone ID, policy TXT/hostname thật, role EC2 hoặc credential root Azure; permission boundary/SCP có thể chặn              |
| `Unable to locate credentials`             | EC2 chưa gắn role/metadata không truy cập được; Azure file phải ở`/root/.aws/credentials` vì dùng sudo           |
| ACME unauthorized / CAA                      | Kiểm tra authoritative DNS, quyền TXT, CAA cho issuer; không chạy hai Certbot cùng lúc                                  |
| Không có plugin                            | Kiểm tra`python3-certbot-dns-route53` và `certbot plugins`; tránh trộn apt/snap                                       |
| NGINX không đọc được certificate       | Cấp thành công trước, cert-name/path đúng, mount cả thư mục symlink; không chmod private key thành world-readable |
| HTTPS timeout                                | SG/NSG/NACL/UFW, cả NSG NIC và subnet, port Docker, trạng thái VM                                                         |
| Connection refused                           | NGINX chưa listen hoặc chưa publish 443; kiểm tra Compose ps và logs                                                     |
| TLS hostname mismatch                        | SAN thiếu shared/origin, sai certificate path, DNS trỏ VM khác; không bỏ verify TLS                                      |
| HTTPS 502                                    | App không ready; Docker resolver/upstream`app:8080`, Compose logs nginx/app                                                |
| Renewal pass nhưng TLS vẫn certificate cũ | Hook reload thất bại, mount sai hoặc request vào origin khác; đối chiếu expiry qua TLS                                |
| HTTP IP sau redirect báo lỗi certificate   | Dùng HTTP/HTTPS hostname, không IP; thay các probe cũ sang HTTPS origin                                                   |

## 12. Khi gặp lỗi: rollback trên máy lỗi; evidence là tùy chọn

### 12.1. CHỈ MÁY GẶP LỖI — khôi phục backup

Nếu hệ thống đang hoạt động thì **bỏ qua toàn bộ lệnh rollback**. Lệnh giống nhau cho hai VM nên ghi một bản: AWS lỗi thì chạy trong SSH EC2 với backup EC2; Azure lỗi thì chạy trong SSH Azure với backup Azure. Không rollback máy còn hoạt động và không dùng nhầm backup của cloud khác.

Nếu lỗi trước khi recreate, sửa file hoặc phục hồi backup; container cũ vẫn có thể đang chạy config đã load. Nếu HTTPS mới không lên, dùng **đường dẫn backup thật đã ghi ở mục 8.1**:

```bash
cd /opt/dcs29
# Thay TEN_BACKUP_THAT bằng tên thư mục đã ghi, không chạy nguyên placeholder.
sudo cp /opt/dcs29/TEN_BACKUP_THAT/compose.yaml /opt/dcs29/compose.yaml
sudo cp /opt/dcs29/TEN_BACKUP_THAT/nginx.conf /opt/dcs29/nginx.conf
sudo docker compose config -q
sudo docker compose run --rm --no-deps nginx nginx -t
sudo docker compose up -d --no-deps --force-recreate --pull never nginx
curl -fsS http://127.0.0.1/health/ready
```

Rollback này quay về HTTP GĐ3/GĐ4 nếu backup lấy trước HTTPS. Không xóa certificate/zone/IP và không triển khai failover để né TLS lỗi. Nếu đã bật DNS failover ở giai đoạn sau, cần rollback theo GD6 thay vì quay về HTTP đơn lẻ.

### 12.2. TÙY CHỌN — evidence cho báo cáo

Để phục vụ báo cáo và lần triển khai lại, thu output trên từng VM và ảnh Console/Portal, rồi lưu ghi chú trên **laptop trong `experiments/`** sau khi che thông tin nhạy cảm. Không cần tạo thư mục repo trên VM. Nội dung có thể gồm:

- Zone ID, delegation/NS, hai A record và TTL; screenshot không chứa secret.
- AWS SG 443, Azure NSG 443 và Public IP tĩnh.
- Cert-name, SAN, issuer, serial/fingerprint, ngày bắt đầu/hết hạn ở mỗi VM.
- Output HTTPS health/version/devices và hostname chung ép IP ở cả hai origin.
- Kết quả dry-run, hook, timer và owner renewal; NGINX TLS config đã thay hostname thật.
- Chi phí domain, hosted zone/query và tài nguyên VM/IP; GD5 chưa tạo health check tính phí GD6.

**CHỈ EC2 — trong SSH EC2, lấy metadata certificate AWS:**

```bash
date -u +%FT%TZ
sudo openssl x509 -in /etc/letsencrypt/live/dcs29-aws/fullchain.pem \
  -noout -issuer -serial -dates -fingerprint -sha256 -ext subjectAltName
```

**CHỈ AZURE — trong SSH Azure, lấy metadata certificate Azure:**

```bash
date -u +%FT%TZ
sudo openssl x509 -in /etc/letsencrypt/live/dcs29-azure/fullchain.pem \
  -noout -issuer -serial -dates -fingerprint -sha256 -ext subjectAltName
```

Không lưu private key, AWS credential hay nguyên thư mục `/etc/letsencrypt` vào Git/evidence.

## 13. Checklist nghiệm thu và bước tiếp theo

| Kiểm tra                                               | AWS                | Azure              |
| ------------------------------------------------------- | ------------------ | ------------------ |
| Origin A record đúng, IP ổn định                   | Bắt buộc         | Bắt buộc         |
| HTTPS origin, chain/hostname hợp lệ                   | Bắt buộc         | Bắt buộc         |
| Shared hostname qua`--resolve`, không bỏ TLS verify | `aws-primary`    | `azure-standby`  |
| `/health/ready`                                       | HTTP 200 qua HTTPS | HTTP 200 qua HTTPS |
| Version/commit/dataset đầy đủ                       | Giống Azure       | Giống AWS         |
| HTTP redirect                                           | 301 tới HTTPS     | 301 tới HTTPS     |
| Renewal dry-run + hook + timer                          | Pass               | Pass               |

- [ ] Domain thật và delegation Route 53 đã kiểm tra từ DNS bên ngoài.
- [ ] Certificate mỗi VM chứa shared hostname và đúng hostname origin.
- [ ] 443 mở, 8080 không public; Compose giữ nguyên app digest/environment/region.
- [ ] Dashboard/API qua HTTPS origin và các lệnh ép shared hostname đều pass.
- [ ] Nếu cần báo cáo: certificate metadata/evidence không secret đã lưu (tùy chọn; bạn hiện chọn bỏ qua).
- [ ] Renewal owner, lịch chạy lệch nhau và kiểm tra expiry đã ghi.

**Xong GD5:** cùng hostname có TLS hợp lệ tại cả hai cloud. Shared hostname chưa được publish DNS theo hướng dẫn này và chưa có chuyển traffic tự động. Sang [GD6 — Route 53 failover](GIAI_DOAN_06_ROUTE53_FAILOVER.md): tạo health checks HTTPS riêng từng origin và hai CNAME failover cùng tên `app`.
