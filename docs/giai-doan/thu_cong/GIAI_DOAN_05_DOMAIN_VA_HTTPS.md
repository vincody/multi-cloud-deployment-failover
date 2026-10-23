# Giai đoạn 5 — Domain chung và HTTPS trên AWS EC2 / Azure VM

**Đầu vào:** GĐ3/GĐ4 đã pass HTTP trên hai origin. Điền `YOUR_DOMAIN`, `AWS_PUBLIC_IP`, `AZURE_PUBLIC_IP`, `AWS_REGION`, `AZURE_REGION`, `CERT_EMAIL` trong [bảng thông số](THONG_SO_TRIEN_KHAI.md). Domain phải thuộc quyền quản lý của bạn; thay mọi ký hiệu mẫu trước khi nhập vào Console, Ingress hoặc chạy lệnh. `aws-origin.YOUR_DOMAIN`, `azure-origin.YOUR_DOMAIN`, `app.YOUR_DOMAIN` là ba hostname nằm trong **cùng một domain**, không phải ba domain phải mua.

**Mục tiêu:** cả hai VM có HTTPS hợp lệ cho hostname origin riêng và hostname chung `app.YOUR_DOMAIN` trước khi GĐ6 tạo DNS failover. AWS dùng IAM role cho Certbot DNS-01; Azure dùng credential DNS giới hạn quyền, lưu ngoài repo. Không chép certificate/private key giữa hai cloud.

Nếu tạm nghỉ giữa GĐ5/GĐ6, có thể stop hai VM; khi bật lại, xác nhận writer, phê duyệt startup guard theo hướng dẫn VM rồi kiểm tra Elastic IP/Static Public IP, pods và certificate. Nếu đã cài watcher, tạo `/opt/dcs29/watcher.pause` trên VM Azure trước bảo trì; chỉ xóa file sau khi baseline khỏe và đã xóa marker incident cũ nếu có. VM tắt thì cả ứng dụng lẫn renewal không chạy. [AWS EC2 lifecycle](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html) · [Azure VM billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Thiết lập DNS, HTTPS và gia hạn certificate riêng trên hai VM. |
| Chuẩn bị | Hai origin HTTP đạt; có domain, IP cố định và email certificate. |
| Hoàn tất khi | TLS hợp lệ cho hai origin và hostname chung; renewal đã dry-run. |

**Luồng thao tác:** [2. Chuẩn bị domain và địa chỉ origin](#2-chuẩn-bị-domain-và-địa-chỉ-origin) → [3. AWS Console — tạo public hosted zone và nối domain](#3-aws-console--tạo-public-hosted-zone-và-nối-domain) → [4. AWS Console — tạo hai A record origin](#4-aws-console--tạo-hai-a-record-origin) → [5. Mở cổng HTTPS ở hai cloud](#5-mở-cổng-https-ở-hai-cloud) → [6. AWS Console — quyền Route 53 cho Certbot](#6-aws-console--quyền-route-53-cho-certbot) → [7. Cấp certificate — làm AWS trước, Azure sau](#7-cấp-certificate--làm-aws-trước-azure-sau) → [8. CẢ HAI CLUSTER — đưa certificate vào TLS Secret và Ingress](#8-cả-hai-cluster--đưa-certificate-vào-tls-secret-và-ingress) → [9. LAPTOP — kiểm tra HTTPS trước khi đổi DNS chung](#9-laptop--kiểm-tra-https-trước-khi-đổi-dns-chung) → [10. Renewal tự động — lệnh chung và lịch riêng cho hai máy](#10-renewal-tự-động--lệnh-chung-và-lịch-riêng-cho-hai-máy) → [13. Checklist nghiệm thu](#13-checklist-nghiệm-thu).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

**Tùy chọn:** redirect HTTP → HTTPS ở mục 9.2; cấp certificate, TLS Secret, kiểm tra và renewal vẫn là luồng chính.

## 1. Ta sẽ làm gì?

```text
aws-origin.YOUR_DOMAIN   → AWS_PUBLIC_IP   → Traefik HTTPS → Service issues-app → pod app:8080
azure-origin.YOUR_DOMAIN → AZURE_PUBLIC_IP  → Traefik HTTPS → Service issues-app → pod app:8080

app.YOUR_DOMAIN: certificate hợp lệ trên CẢ HAI VM
                kiểm tra bằng curl --resolve trước khi tạo DNS failover
```

Route 53 chỉ trả lời DNS. Khi đổi đích từ AWS sang Azure, trình duyệt vẫn gửi TLS SNI và HTTP Host là `app.YOUR_DOMAIN`. Do đó certificate trên mỗi VM phải chứa hostname chung này.

Quy trình chính dùng **Certbot + ACME DNS-01 + plugin Route 53**. Certbot chạy trên Ubuntu host, tự thêm/xóa TXT xác thực ở Route 53; certificate được đưa vào TLS Secret để Traefik trong K3s phục vụ HTTPS. AWS dùng IAM role; Azure dùng IAM user riêng có quyền DNS giới hạn cho lab. Hai VM cấp certificate riêng, không sao chép private key giữa cloud. Không cần ALB/Azure Container Apps. Các bước DNS, quyền Route 53 và Certbot dưới đây được giữ chi tiết; chỉ phần đưa certificate vào runtime đổi sang K3s.

**Thứ tự:** tạo hosted zone → đợi domain được duyệt → đổi bốn NS tại nhà đăng ký tên miền → kiểm tra NS công khai → tạo hai A record origin → mở 443 → quyền DNS → certificate AWS → TLS Secret/Ingress AWS → lặp lại Azure → renewal → nghiệm thu. Chưa tạo record `app` hoặc health check trong GD5. Browser sẽ kiểm tra hai hostname origin; hostname chung được kiểm tra bằng `curl --resolve`.

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
5. Mở 443, cấp quyền DNS-01, cấp certificate riêng trên AWS rồi Azure, cấu hình Ingress/TLS Secret và kiểm tra HTTPS/renewal.

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
| Priority                | `320` nếu chưa dùng (HTTP 310, WireGuard 330); chọn số trống đứng trước rule Deny liên quan |
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

Không bật UFW mới trong bước này khi chưa chuẩn bị rule SSH. Mở firewall chưa đủ để 443 hoạt động: phải cấu hình Traefik/Ingress/TLS Secret ở mục 8. Tham khảo [Microsoft: quản lý rule NSG](https://learn.microsoft.com/en-us/azure/virtual-network/manage-network-security-group).

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

Không chạy `cat` file để lấy evidence. Giữ file trên host cho renewal; không mount credential vào pod app/Traefik. Sau lab, khi không còn cần renewal, deactivate rồi xóa key. Muốn bỏ long-term key ở môi trường lâu dài, thiết kế credential tạm như IAM Roles Anywhere riêng; không nằm trong bước lab này.

## 7. Cấp certificate — làm AWS trước, Azure sau

### 7.1. CẢ HAI MÁY — cài Certbot, cùng lệnh

Chạy đủ các lệnh dưới trên **SSH EC2 trước**, rồi lặp lại trên **SSH Azure**. Không chạy trên PowerShell laptop hoặc trong container. Có thể đứng ở bất kỳ thư mục nào:

```bash
sudo apt-get update
sudo apt-get install -y certbot python3-certbot-dns-route53
sudo certbot --version
sudo certbot plugins
sudo systemctl stop certbot.timer
```

Danh sách plugin phải có `dns-route53`. Dừng timer trên **cả hai VM** trước cấp/dry-run thủ công để lịch nền không sửa TXT song song; nếu certbot.service đang chạy, đợi nó hoàn tất trước tiếp tục. Bật timer lại sau khi đặt lịch ở mục 10.5. Khi lỗi giữa chừng, giữ lịch tạm dừng trong lúc sửa, rồi nghiệm thu và bật lại để không bỏ quên renewal. Hướng dẫn dùng package Ubuntu; không trộn thêm bản snap/pip nếu đã cài Certbot theo cách khác. Nếu plugin thiếu, kiểm tra package/source trước khi cấp certificate.

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
sudo env AWS_PROFILE=default AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials certbot certonly --dns-route53 \
  --cert-name dcs29-azure \
  -d app.YOUR_DOMAIN \
  -d azure-origin.YOUR_DOMAIN \
  --email CERT_EMAIL --agree-tos --non-interactive
sudo certbot certificates
```

Đường dẫn tương ứng là `/etc/letsencrypt/live/dcs29-azure/`. Hai certificate có cùng SAN `app...`, nhưng hostname origin, serial và private key có thể khác nhau; đó là kết quả mong đợi.

Certbot tự xử lý TXT DNS-01. Cấp lần lượt trên hai VM, kể cả khi renewal thử nghiệm, để tránh cùng sửa TXT `_acme-challenge.app...`. Không cần record A của `app` để cấp certificate DNS-01. Nếu CAA đang hạn chế issuer, chủ domain phải cho phép Let's Encrypt. Plugin cần outbound tới AWS API và ACME. [Plugin Route 53](https://certbot-dns-route53.readthedocs.io/en/stable/) mô tả cơ chế credential và DNS challenge.

**Dừng nếu cấp certificate thất bại:** chưa tạo TLS Secret tham chiếu tới file không tồn tại. Không cấp certificate lặp nhiều lần khi chưa sửa lỗi để tránh rate limit.

## 8. CẢ HAI CLUSTER — đưa certificate vào TLS Secret và Ingress

Điều kiện: GĐ4 đã có namespace issues, Service issues-app và schema/replication. Các bước 2–7 phía trên vẫn chạy tại Console/Portal/SSH đúng như hướng dẫn. Không tạo lại NGINX Compose.

### 8.1. Kiểm tra namespace, Service và IngressClass

Trên SSH từng VM:

```bash
sudo k3s kubectl get ingressclass
sudo k3s kubectl -n issues get pods,svc
sudo certbot certificates
```

Mẫu dưới dùng IngressClass traefik mặc định K3s. Certificate phải gồm app.YOUR_DOMAIN và hostname origin đúng của VM, chưa hết hạn. Nếu Service chưa có endpoints Ready, quay lại GĐ4, không che lỗi bằng sửa TLS.

### 8.2. Import certificate — mỗi VM dùng đường dẫn riêng

**CHỈ EC2:**

```bash
sudo k3s kubectl -n issues create secret tls issues-tls --cert=/etc/letsencrypt/live/dcs29-aws/fullchain.pem --key=/etc/letsencrypt/live/dcs29-aws/privkey.pem --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

**CHỈ AZURE:**

```bash
sudo k3s kubectl -n issues create secret tls issues-tls --cert=/etc/letsencrypt/live/dcs29-azure/fullchain.pem --key=/etc/letsencrypt/live/dcs29-azure/privkey.pem --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Không tách pipeline để in/lưu YAML Secret ra repo; không copy private key giữa cloud. Kiểm tra metadata bằng kubectl get secret issues-tls, không export nội dung Secret.

### 8.3. Tạo Ingress HTTPS — ví dụ AWS

Trên SSH AWS, mở editor:

```bash
cd /opt/dcs29
nano ingress-https.yaml
```

Thay toàn bộ YOUR_DOMAIN trước khi dán. Ctrl+O → Enter → Ctrl+X để lưu.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: issues-https
  namespace: issues
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: websecure
    traefik.ingress.kubernetes.io/router.tls: "true"
spec:
  ingressClassName: traefik
  tls:
    - secretName: issues-tls
      hosts:
        - aws-origin.YOUR_DOMAIN
        - app.YOUR_DOMAIN
  rules:
    - host: aws-origin.YOUR_DOMAIN
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: issues-app
                port:
                  number: 80
    - host: app.YOUR_DOMAIN
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: issues-app
                port:
                  number: 80
```

Trên Azure tạo cùng file nhưng đổi cả hai chỗ aws-origin thành azure-origin; giữ app.YOUR_DOMAIN, tên Service và TLS Secret. Không dùng bản AWS nguyên xi trên Azure.

### 8.4. Dry-run và áp dụng trên từng VM

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/ingress-https.yaml
sudo k3s kubectl apply -f /opt/dcs29/ingress-https.yaml
sudo k3s kubectl -n issues get ingress
sudo k3s kubectl -n issues describe ingress issues-https
```

Traefik/ServiceLB phải lắng nghe 443 trên VM và SG/NSG đã cho phép. Không thêm publish port Docker. Nguồn: [Kubernetes TLS Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/), [Traefik Ingress annotations](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress/).

## 9. LAPTOP — kiểm tra HTTPS trước khi đổi DNS chung

PowerShell laptop, thay domain/IP thật:

```powershell
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/api/database/status
```

AWS primary, Azure standby; cùng commit/digest. /health/database Azure trả 503 bình thường khi standby; không dùng curl --fail ở endpoint đó để nghiệm thu TLS.

### 9.1. Kiểm tra hostname chung ép tới mỗi VM

```powershell
$SharedHost = 'app.YOUR_DOMAIN'
$AwsIp = 'AWS_PUBLIC_IP'
$AzureIp = 'AZURE_PUBLIC_IP'
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/issues.html"
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/issues.html"
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/api/issues"
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/api/issues"
```

Không dùng -k để bỏ kiểm tra TLS. --resolve chỉ thử từng origin, không chứng minh DNS failover. Người dùng mở /issues.html; / tự chuyển hướng tới trang này.

### 9.2. Redirect HTTP → HTTPS khi cần

Sau khi HTTPS kiểm tra thành công, xác nhận cluster có CRD Middleware của Traefik:

```bash
sudo k3s kubectl get crd middlewares.traefik.io
```

Tạo `/opt/dcs29/redirect-https.yaml` trên từng VM. Mẫu dùng Traefik CRD `traefik.io/v1alpha1`; đối chiếu version đang chạy trước khi apply:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: redirect-https
  namespace: issues
spec:
  redirectScheme:
    scheme: https
    permanent: true
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: issues-http
  namespace: issues
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
    traefik.ingress.kubernetes.io/router.middlewares: issues-redirect-https@kubernetescrd
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

Apply sẽ cập nhật Ingress issues-http đã tạo ở GĐ4, không thêm một ingress HTTP cạnh tranh:

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/redirect-https.yaml
sudo k3s kubectl apply -f /opt/dcs29/redirect-https.yaml
```

PowerShell laptop:

```powershell
curl.exe -I http://aws-origin.YOUR_DOMAIN/version
curl.exe -I http://azure-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body -L http://aws-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body -L http://azure-origin.YOUR_DOMAIN/version
```

Kỳ vọng redirect tới HTTPS cùng hostname/path; -L trả JSON đúng cloud, không lỗi TLS hoặc vòng lặp. Nếu CRD thiếu/dry-run lỗi, xử lý Traefik theo version trước, không dán cấu hình NGINX cũ vào cluster. Giữ cổng 80 cho redirect; DNS-01 không cần cổng này để xác thực certificate. Nguồn: [Traefik RedirectScheme](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/redirectscheme/).

Giữ scheme/hostname/port chung xuyên failover để localStorage giữ bản nháp. Chuyển GĐ6 chỉ khi TLS/Service/DB được kiểm tra trên cả hai origin.

## 10. Renewal tự động — lệnh chung và lịch riêng cho hai máy

Certificate có hạn; ghi ngày hết hạn thực tế từ `certbot certificates`. Certbot cần quyền DNS còn hoạt động và hook cập nhật TLS Secret sau khi gia hạn.

**Quy ước:** mục ghi **CẢ HAI MÁY** thì dùng đúng cùng lệnh/nội dung trên SSH EC2 và SSH Azure; không cần đổi gì. Mục ghi **CHỈ EC2** hoặc **CHỈ AZURE** chỉ thực hiện trên máy đó. Tất cả lệnh mục 10 chạy trong SSH, không phải PowerShell laptop; có thể bắt đầu từ bất kỳ thư mục nào vì đường dẫn đã tuyệt đối hoặc hook tự `cd`.

| Bước | Chạy ở đâu? | Có khác nhau không? |
|---|---|---|
| 10.1 Tạo và thử hook | Cả hai máy | Không; nội dung giống nhau |
| 10.2 Dry-run renewal | Mỗi máy dùng lệnh riêng | Khác cert-name |
| 10.3 Kiểm tra unit | Cả hai máy | Không; timer còn dừng |
| 10.4 Chỉnh lịch | EC2 và Azure có block riêng | Khác giờ chạy |
| 10.5 Bật timer/kiểm tra | Cả hai máy | Lệnh giống, lịch kết quả khác |

### 10.1. CẢ HAI MÁY — hook cập nhật TLS Secret

Trên SSH từng VM:

```bash
sudo install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
sudo nano /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s
```

Dán vào editor, không dán trực tiếp vào shell:

```sh
#!/bin/sh
set -eu
case "${RENEWED_LINEAGE:-}" in
  /etc/letsencrypt/live/dcs29-aws|/etc/letsencrypt/live/dcs29-azure) ;;
  *) exit 0 ;;
esac
/usr/local/bin/k3s kubectl -n issues create secret tls issues-tls \
  --cert="$RENEWED_LINEAGE/fullchain.pem" \
  --key="$RENEWED_LINEAGE/privkey.pem" \
  --dry-run=client -o yaml | /usr/local/bin/k3s kubectl apply -f -
```

Ctrl+O → Enter → Ctrl+X. Sau đó:

```bash
sudo chmod 750 /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s
```

Thử hook riêng với **certificate production hiện có**, không dùng certificate staging để thay TLS Secret.

**EC2:**

```bash
sudo env RENEWED_LINEAGE=/etc/letsencrypt/live/dcs29-aws /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s
```

**Azure:**

```bash
sudo env RENEWED_LINEAGE=/etc/letsencrypt/live/dcs29-azure /etc/letsencrypt/renewal-hooks/deploy/20-dcs29-k3s
```

Kiểm tra HTTPS lại sau apply. Hook cập nhật Secret của cluster tại VM đó, không restart app. Nếu hook NGINX cũ còn trên máy chuyển đổi, kiểm tra và gỡ đúng hook cũ để renewal không gọi Compose đã bỏ. Khi K3s/API không hoạt động lúc renewal, phải retry import certificate sau khi phục hồi; không coi Certbot success là Secret chắc chắn đã cập nhật.

### 10.2. MỖI MÁY MỘT LỆNH — thử renewal

**CHỈ EC2:**

```bash
sudo certbot renew --cert-name dcs29-aws --dry-run
```

Đợi EC2 hoàn tất thành công rồi chuyển sang Azure, không chạy đồng thời vì hai máy cùng xác thực TXT của `app.YOUR_DOMAIN`.

**CHỈ AZURE:**

```bash
sudo env AWS_PROFILE=default AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials certbot renew --cert-name dcs29-azure --dry-run
```

Kỳ vọng thông báo simulated renewal thành công. Nếu lỗi, xử lý quyền Route 53/credential trước khi nghiệm thu. Dry-run dùng certificate thử nghiệm, không thay production certificate. Nó mặc định không chạy deploy hook, nên ta đã thử hook riêng ở 10.1. [Certbot: renewal và hooks](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates).

### 10.3. CẢ HAI MÁY — kiểm tra unit trước đặt lịch

Chạy cả cụm dưới trên EC2, rồi chạy cùng cụm trên Azure:

```bash
sudo systemctl cat certbot.timer
sudo systemctl cat certbot.service
```

Timer còn dừng trong lúc đặt lịch. Nếu pager vẫn mở, nhấn `q` để thoát. Nếu báo không tìm thấy unit, kiểm tra cách cài Certbot/package; không tạo scheduler thứ hai khi chưa rõ lịch hiện có.

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
Persistent=false
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
Persistent=false
```

Lưu theo editor như phần A. Không copy block 00/12 UTC của EC2 vào Azure.

**Ý nghĩa các dòng chung:** `OnCalendar=` để trống là có chủ ý, xóa lịch mặc định trước khi đặt lịch mới. `RandomizedDelaySec=0` bỏ độ trễ ngẫu nhiên để hai lịch giữ cách nhau. `Persistent=false` tránh hai VM cùng chạy bù lịch missed ngay khi bật sau thời gian nghỉ. Các dòng cấu hình không bắt đầu bằng `#`. Lịch lệch giờ giảm xung đột, không phải distributed lock.

Trên cả hai VM, tạo override giới hạn thời gian chạy service để một lượt bất thường không kéo sang lịch peer:

```bash
sudo install -d /etc/systemd/system/certbot.service.d
sudo tee /etc/systemd/system/certbot.service.d/20-dcs29-timeout.conf >/dev/null <<'EOF'
[Service]
TimeoutStartSec=45min
EOF
```

**Chỉ Azure:** tạo thêm override cho credential DNS của scheduler; không đưa access key vào unit:

```bash
sudo tee /etc/systemd/system/certbot.service.d/30-dcs29-dns-env.conf >/dev/null <<'EOF'
[Service]
Environment=AWS_PROFILE=default
Environment=AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials
EOF
```

### 10.5. CẢ HAI MÁY — áp dụng lịch và kiểm tra

Sau khi lưu override, chạy cùng cụm sau trên EC2 và Azure:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now certbot.timer
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

## 12. Rollback cấu hình và lưu evidence

Trước khi sửa Ingress, copy file YAML nguồn sang bản backup có tên riêng trong /opt/dcs29; không export Secret có private key vào thư mục repo. Nếu Ingress lỗi, khôi phục file nguồn đúng VM và dry-run/apply lại, kiểm tra /version và /api/issues. Không rollback database/PVC để sửa TLS.

Giữ bằng chứng delegation/A records, certificate SAN/expiry (không private key), TLS origin/shared ép IP, Ingress metadata, Certbot dry-run/timer/hook và thời gian UTC. Các bước Console/DNS/Certbot ở mục 2–7 được thực hiện đầy đủ trên tài khoản của lần chạy này.

## 13. Checklist nghiệm thu

- [ ] Domain delegation/A records đúng IP mới của hai VM.
- [ ] Mục quyền Route 53 và DNS-01 hoàn tất trên đúng EC2/Azure.
- [ ] Certificate origin/shared hợp lệ ở cả hai bên.
- [ ] Ingress/Service hoạt động, API đọc schema được; HTTPS không bỏ verify.
- [ ] Hook cập nhật TLS Secret, dry-run và lịch renewal đã kiểm tra.
- [ ] Redirect chỉ đánh dấu khi đã triển khai/thử; dùng HTTPS chung để giữ origin bản nháp.

Tiếp [GĐ6 — chuyển database và DNS](GIAI_DOAN_06_ROUTE53_FAILOVER.md). TLS origin healthy không tự chứng minh database là writer.

---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## 11. Lỗi thường gặp — kiểm tra theo thứ tự

<details>
<summary>Khi gặp lỗi — mở hướng dẫn</summary>

| Lỗi | Kiểm tra |
|---|---|
| DNS không resolve | Delegation NS, hosted zone, origin A record, resolver cache |
| Certbot DNS-01 lỗi | IAM role/user, TXT policy, credential Azure, không chạy hai renewal cùng lúc |
| TLS certificate mặc định/sai host | issues-tls, SAN, IngressClass/entrypoint và host rules |
| HTTPS timeout | SG/NSG/UFW, Traefik/ServiceLB, VM Running |
| HTTP 404 | Host/path Ingress, Service name/port |
| HTTP 502/503 | Service endpoints, pod Ready, DATABASE_URL/schema |
| Standby /health/database 503 | Trạng thái writer readiness bình thường, kiểm tra /api/database/status |
| Renew success nhưng cert cũ | Hook chưa chạy/apply lỗi, Secret chưa cập nhật, thử hook đúng VM |

</details>
