# Giai đoạn 6 — Route 53 health checks và DNS failover

**Trạng thái (28/09/2026):** Đã xác nhận đường chính AWS → Azure → AWS bằng thử dừng app AWS. DNS authoritative đã trả `azure-origin` và endpoint chung trả `azure-standby` trong lúc AWS lỗi; sau khi AWS phục hồi, DNS authoritative trả `aws-origin` và endpoint chung trả `aws-primary`. Chưa có bằng chứng đã thử dừng app Azure riêng ở mục 4.3 hoặc đo timeline GD8. Evidence lưu file được bỏ qua theo lựa chọn của bạn.
**Mục tiêu:** người dùng vào `https://app.cloudfailover.id.vn`, bình thường được AWS phục vụ; khi AWS unhealthy và Azure healthy, DNS chuyển sang Azure, rồi trở lại AWS khi AWS phục hồi.

> **Nếu vừa nghỉ và đã stop hai máy:** Start EC2 và Azure VM, đợi Running và kiểm tra HTTPS ở mục 2. Không cần cấp lại certificate hay tạo lại zone nếu cấu hình còn hoạt động. Trong toàn bộ bài thử, Azure phải đang chạy; máy standby tắt thì không thể phục vụ khi AWS lỗi.

**Kết quả quan sát trực tiếp:** khi AWS app dừng, AWS origin trả 502 nhưng Azure origin và `app.cloudfailover.id.vn` trả 200, environment `azure-standby`. Sau phục hồi, hai origin readiness đều 200; khoảng một nhịp health check sau, Route 53 và endpoint chung trở lại `aws-primary`. Browser cũ có thể giữ kết nối AWS và tiếp tục thấy 502 cho tới khi mở browser mới. Đây là hành vi cache/connection cần nêu trong báo cáo, không phải record failover sai.

## 1. Hiểu mô hình và nơi thao tác

```text
Người dùng → app.cloudfailover.id.vn
               ├─ Primary CNAME   → aws-origin.cloudfailover.id.vn   → 18.143.30.46
               └─ Secondary CNAME → azure-origin.cloudfailover.id.vn → 172.198.68.77

Route 53 health check AWS   → HTTPS AWS origin /health/ready
Route 53 health check Azure → HTTPS Azure origin /health/ready
```

GD5 đã chuẩn bị TLS cho `app.cloudfailover.id.vn` trên cả hai VM. GD6 mới tạo DNS cho tên `app`. DNS đổi IP đích nhưng browser vẫn dùng hostname `app`, nên certificate của cả hai VM phải nhận tên đó.

| Nhãn | Nơi thực hiện |
|---|---|
| AWS CONSOLE | Trình duyệt laptop, Route 53 trong tài khoản đang chứa hosted zone |
| AZURE PORTAL | Trình duyệt laptop, chỉ để Start/kiểm tra VM nếu cần |
| LAPTOP | PowerShell Windows ngoài SSH, kiểm tra DNS và gọi ứng dụng |
| CHỈ EC2 | SSH Ubuntu EC2 `18.143.30.46`, gây lỗi/khôi phục AWS |
| CHỈ AZURE | SSH Ubuntu Azure `172.198.68.77`, thử health Azure |
| CẢ HAI MÁY | Lệnh chung, thực hiện lần lượt trong SSH EC2 rồi SSH Azure |

**Hai health check, kể cả check Azure, đều tạo trên AWS Route 53**, không tạo trên Azure Portal. Không cần sửa Compose/NGINX hay chạy lại Certbot trong GD6 nếu GD5 đã pass.

Route 53 là DNS, không phải reverse proxy. Nó không chuyển connection đang mở; client có thể còn cache AWS sau khi DNS authoritative đã chọn Azure. Cấu hình này không bảo đảm zero downtime. [AWS: active-passive failover](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html).

## 2. LAPTOP — kiểm tra đầu vào trước khi tạo health check

### 2.1. Bật và kiểm tra hai origin

- AWS Console → EC2 → Instances → máy GĐ3 → **Start instance** nếu đang tắt.
- Azure Portal → Virtual machines → máy GĐ4 → **Start** nếu đang tắt.
- Đợi Running, kiểm tra IP vẫn đúng bảng dưới. Nếu IP thay đổi, sửa A record origin và các lệnh theo IP thực tế; không giữ IP cũ. AWS cần Elastic IP, Azure cần Static Public IP.

| Thông tin | AWS | Azure |
|---|---|---|
| Origin | `aws-origin.cloudfailover.id.vn` | `azure-origin.cloudfailover.id.vn` |
| IPv4 đã cung cấp | `18.143.30.46` | `172.198.68.77` |
| `/version` environment | `aws-primary` | `azure-standby` |
| Certificate từ GD5 | `dcs29-aws` | `dcs29-azure` |

Trên **PowerShell laptop**, chạy lần lượt:

```powershell
Resolve-DnsName aws-origin.cloudfailover.id.vn -Type A -Server 1.1.1.1
Resolve-DnsName azure-origin.cloudfailover.id.vn -Type A -Server 1.1.1.1
curl.exe --fail-with-body https://aws-origin.cloudfailover.id.vn/health/ready
curl.exe --fail-with-body https://azure-origin.cloudfailover.id.vn/health/ready
curl.exe --fail-with-body https://aws-origin.cloudfailover.id.vn/version
curl.exe --fail-with-body https://azure-origin.cloudfailover.id.vn/version
```

DNS trả đúng IP, readiness thành công, environment đúng cloud, version/commit/dataset đầy đủ khớp. Không dùng `-k` để bỏ TLS verify.

Nếu container chưa chạy, trong **SSH của máy gặp lỗi** dùng cùng lệnh:

```bash
cd /opt/dcs29
sudo docker compose up -d --pull never
sudo docker compose ps
```

### 2.2. Xác nhận TLS hostname chung vẫn hợp lệ

**LAPTOP**, cùng tab PowerShell:

```powershell
$SharedHost = "app.cloudfailover.id.vn"
$AwsIp = "18.143.30.46"
$AzureIp = "172.198.68.77"
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AwsIp}" "https://${SharedHost}/version"
curl.exe --fail-with-body --resolve "${SharedHost}:443:${AzureIp}" "https://${SharedHost}/version"
```

`--resolve` chỉ dùng trong bước kiểm tra từng VM này. Các phép thử failover ở mục 6–8 phải gọi shared hostname qua DNS thật, không dùng `--resolve`.

### 2.3. Quyền và chi phí

Dùng tài khoản/role quản trị đang làm trên AWS Console, có quyền tạo health check và chỉnh record. **Không dùng IAM user `dcs29-azure-certbot`** cho các bước quản trị: policy GD5 chỉ cho sửa TXT ACME, không cho tạo CNAME/health check.

Health checks là tài nguyên có phí, gồm các tùy chọn bổ sung như HTTPS và fast interval tùy bảng giá; Azure là endpoint ngoài AWS nên phải đối chiếu đúng nhóm giá. Xem [Route 53 pricing](https://aws.amazon.com/route53/pricing/) trước khi tạo. Hướng dẫn dùng interval chuẩn 30 giây, bỏ latency measurement/alarm chưa cần thiết. Stop VM không tự xóa health check hoặc ngừng phí hosted zone.

## 3. AWS CONSOLE — tạo hai health check độc lập

### 3.1. Health check AWS — tạo trước

1. Mở **AWS Console → Route 53 → Health checks → Create health check**.
2. Nếu đã có check đúng cấu hình, mở kiểm tra thay vì tạo trùng. Giao diện Route 53 có thể là bản mới hoặc cũ, tên trường hơi khác.
3. **Theo đúng giao diện trong `errors_fix/image.png` của bạn:** chọn **Resource = Endpoint → Specify endpoint by = Domain name**. Ở hàng **Domain name**, bên trái là menu **HTTPS**, bên phải là ô đang chứa `aws-origin.cloudfailover.id.vn` trong ảnh. Không có ô Path riêng trong phần ảnh bạn gửi. Chọn HTTPS, rồi sửa **ô bên phải menu HTTPS** thành:

   ```text
   aws-origin.cloudfailover.id.vn:443/health/ready
   ```

   Ảnh mới ở `errors_fix/zen_fsWRZYukwM.png` minh họa trực tiếp cho **Domain name** bằng `www.example.com:443/images`. Vì vậy dùng cùng mẫu `hostname:443/path`. Không thêm `https://` vì đã chọn HTTPS ở menu bên trái; không nhập `app.cloudfailover.id.vn` vì tên chung này sẽ đổi đích khi failover. Đây là cách điền cho **UI một ô trong ảnh**, không áp dụng cho UI cũ có ô Domain name và Path riêng. Trong giao diện một ô này phải ghi rõ `:443` theo đúng ví dụ AWS; nếu dùng giao diện cũ có ô Port riêng, điền `443` vào ô đó.

   | Trường/điều cần kiểm tra | Giá trị AWS |
   |---|---|
   | Name | `dcs29-aws-https-ready` |
   | Resource | **Endpoint** |
   | Specify endpoint by | **Domain name** |
   | Protocol trong menu | **HTTPS** |
   | Ô bên phải menu HTTPS trong ảnh | `aws-origin.cloudfailover.id.vn:443/health/ready` |
   | Port | `443`, nằm giữa hostname và path trong cùng ô |

   **Nếu Console của bạn hiển thị UI khác:** ô Domain name riêng chỉ nhập `aws-origin.cloudfailover.id.vn`, ô Path/Resource path riêng nhập `/health/ready`. Không nhập URL đầy đủ `https://...` vào ô chỉ nhận hostname.
4. Mở **Advanced configuration / Additional settings**:

| Trường | Chọn |
|---|---|
| Request interval | **Standard / 30 seconds** |
| Failure threshold | `3` |
| Enable SNI | **Bật** |
| Host name nếu có ô riêng | `aws-origin.cloudfailover.id.vn` |
| String matching / Search string | **Tắt**, để trống |
| Latency measurement / Latency graphs | **Tắt** |
| Health checker regions | Giữ recommended/default |
| Invert health check status | **Tắt** |
| Disabled | **Tắt**, check phải được enabled |

5. Trước khi tạo, xem phần review/preview nếu có: endpoint phải là HTTPS đến `aws-origin.cloudfailover.id.vn` và path `/health/ready`, không phải `/`. Nếu preview không hiển thị path, sau khi tạo mở chi tiết check → cấu hình endpoint để xác nhận **Resource path = `/health/ready`**. Nếu AWS lưu path là `/`, sửa health check ngay trước khi tạo record failover; không coi check healthy là đủ vì `/` cũng có thể trả 200. Nếu có bước **Create alarm**, chọn **No / Không tạo alarm** trong GD6. Chọn **Next/Create health check** theo giao diện.
6. Mở check vừa tạo, ghi **Health check ID** vào ghi chú cá nhân (không phải Hosted zone ID). Đợi trạng thái cập nhật, không kết luận lỗi ngay khi vừa tạo.

Nếu Console đang dùng form **IP address**: IP AWS là `18.143.30.46`, vẫn phải có hostname origin trong ô Host name/Domain name và bật SNI; không dùng IP làm TLS hostname. Chỉ chọn một cách cấu hình và ghi lại cách đã chọn.

Tham khảo [AWS: tạo health check](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating.html) và [các trường cấu hình](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-values.html).

### 3.2. Health check Azure — cũng tạo trên AWS Console

Vẫn trong **Route 53 → Health checks → Create health check**. Với UI một ô như ảnh, chọn HTTPS và nhập `azure-origin.cloudfailover.id.vn:443/health/ready` vào ô bên phải menu HTTPS. Lặp lại mục 3.1 với **các giá trị khác** sau:

| Trường | Giá trị Azure |
|---|---|
| Name | `dcs29-azure-https-ready` |
| Ô Domain name của UI một ô trong ảnh | `azure-origin.cloudfailover.id.vn:443/health/ready` |
| Host name nếu có ô riêng | `azure-origin.cloudfailover.id.vn` |
| IP nếu chọn form IP address | `172.198.68.77` |

**Các giá trị còn lại giống AWS:** HTTPS, 443, `/health/ready`, interval 30, threshold 3, SNI bật; string matching/latency/alarm/invert tắt. Lưu check, mở chi tiết để xác nhận **Resource path = `/health/ready`**, rồi ghi Health check ID Azure riêng. Nếu UI cũ có ô riêng, Domain name chỉ là `azure-origin.cloudfailover.id.vn`, Path là `/health/ready`.

### 3.3. Chỉ tiếp tục khi cả hai healthy

Trong **Health checks**, mở từng check → **Status / Health checkers**; refresh để xem tình trạng và lý do nếu fail. Đợi cả hai healthy ổn định qua vài lần cập nhật.

Nếu laptop gọi được mà checker không gọi được, đối chiếu TCP 443 của SG AWS và NSG Azure vẫn cho phép Internet như GD5, không chỉ IP laptop. Kiểm tra path đúng, SNI bật, domain DNS đúng và VM đang Running. Không mở 8080.

**Health check healthy không thay thế kiểm tra TLS của GD5:** Route 53 HTTPS check không xác thực certificate như browser/curl; vẫn phải kiểm tra expiry/hostname/chain từ ngoài bằng curl không bỏ verify. Không chỉnh threshold để che lỗi. [AWS: cách xác định health và giới hạn TLS](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-determining-health-of-endpoints.html).

## 4. Thử health check riêng trước khi publish shared domain

Bước này kiểm tra mỗi check thực sự theo đúng origin. Chỉ dừng **app**, giữ NGINX/VM hoạt động; chưa phải bài stop toàn EC2 của GD8.

### 4.1. CHỈ EC2 — dừng app AWS

Trong **SSH EC2**:

```bash
cd /opt/dcs29
date -u +%FT%TZ
sudo docker compose stop app
sudo docker compose ps
```

Trên **LAPTOP**:

```powershell
curl.exe --fail-with-body --connect-timeout 5 --max-time 10 https://aws-origin.cloudfailover.id.vn/health/ready
curl.exe --fail-with-body https://azure-origin.cloudfailover.id.vn/health/ready
```

AWS phải thất bại (thường 502 vì app dừng); Azure vẫn thành công. AWS Console → Health checks: đợi AWS unhealthy, Azure vẫn healthy. Không suy thời gian chắc chắn là 30 × 3 = 90 giây; phải quan sát thực tế.

### 4.2. CHỈ EC2 — khôi phục ngay sau khi kiểm tra

Trong **SSH EC2**:

```bash
cd /opt/dcs29
sudo docker compose up -d --no-deps --pull never app
sudo docker compose ps
```

Gọi lại AWS readiness trên laptop, đợi AWS check healthy rồi mới thử Azure. Không dùng `docker compose down` trong bài này.

### 4.3. CHỈ AZURE — thử tương tự, rồi khôi phục

Chỉ khi AWS đã healthy, trong **SSH Azure** chạy:

```bash
cd /opt/dcs29
sudo docker compose stop app
```

LAPTOP gọi hai readiness như 4.1: AWS vẫn thành công, Azure thất bại. AWS Console phải chỉ có check Azure chuyển unhealthy.

Sau khi xác nhận, **SSH Azure** khôi phục:

```bash
cd /opt/dcs29
sudo docker compose up -d --no-deps --pull never app
sudo docker compose ps
```

Đợi cả hai readiness và hai checks healthy trước mục 5. Các lệnh stop/start giống nhau nhưng chạy ở phiên SSH riêng của origin đang thử; không dừng cả hai cùng lúc.

## 5. AWS CONSOLE — tạo hai record failover tên app

### 5.1. Kiểm tra xung đột trước khi tạo

**Route 53 → Hosted zones → cloudfailover.id.vn → Records**, lọc tên `app.cloudfailover.id.vn`.

Theo GD5 chưa có DNS record `app`, nên thường không có gì cần xóa. Nếu có record A/AAAA/CNAME simple cũ cho `app`, lưu cấu hình cũ trước và xác định nó có đang dùng không. CNAME không được cùng tồn tại với A/AAAA ở cùng tên; simple CNAME cũng không trộn với cặp failover. Chỉ thay record `app` liên quan, không xóa NS/SOA hoặc A record hai origin.

### 5.2. Primary — AWS

Chọn **Create record**; nếu mở wizard, chọn **Failover routing**. Form thường có thể nằm ở **Switch to quick create**. Điền:

| Trường | Giá trị |
|---|---|
| Record name | `app` (giao diện thêm `.cloudfailover.id.vn`) |
| Record type | **CNAME** |
| Alias | **Off** |
| Value | `aws-origin.cloudfailover.id.vn` |
| TTL | `30` giây |
| Routing policy | **Failover** |
| Failover record type / Role | **Primary** |
| Record ID / Set identifier | `aws-primary` |
| Associate with health check | **Yes** nếu có tùy chọn |
| Health check | Chọn `dcs29-aws-https-ready` / đúng Health check ID AWS |

Bấm **Create records**. Value là hostname origin, không phải IP, không có `https://` hoặc `/version`. Không để record name trống: đó là apex, không dùng CNAME được.

### 5.3. Secondary — Azure

Chọn **Create record** lần nữa, điền các giá trị giống Primary ngoại trừ:

| Trường | Giá trị |
|---|---|
| Record name | Vẫn là `app` |
| Value | `azure-origin.cloudfailover.id.vn` |
| Failover record type / Role | **Secondary** |
| Record ID / Set identifier | `azure-secondary` |
| Health check | `dcs29-azure-https-ready` / đúng ID Azure |

Type vẫn **CNAME**, Alias **Off**, TTL **30**, Routing **Failover**, gắn health check **Yes**. Không đặt record name là `app-azure`; hai record phải cùng tên để Route 53 lựa chọn.

Bấm **Create records**. Danh sách cuối phải có **hai dòng CNAME cùng tên `app.cloudfailover.id.vn`**, khác role/identifier/value/check. Đây là hợp lệ với routing Failover. Không phải hai A record của origin; chúng vẫn giữ ở zone. Xem [AWS: giá trị failover record](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resource-record-sets-values-failover.html).

## 6. LAPTOP — kiểm tra bình thường, traffic vào AWS

Hai check phải healthy. Trên **PowerShell laptop**:

```powershell
$SharedHost = "app.cloudfailover.id.vn"
$AuthoritativeNs = "ns-1139.awsdns-14.org"
Resolve-DnsName $SharedHost -Type CNAME -Server $AuthoritativeNs -DnsOnly
Resolve-DnsName $SharedHost -Type A -Server 1.1.1.1
curl.exe --fail-with-body "https://${SharedHost}/health/ready"
curl.exe --fail-with-body "https://${SharedHost}/version"
```

Kỳ vọng CNAME là `aws-origin.cloudfailover.id.vn`, IP A theo chuỗi CNAME là `18.143.30.46`, `/version` báo `aws-primary`. Nếu query `app` trước khi record tồn tại, resolver có thể còn cache NXDOMAIN; authoritative query giúp phân biệt với record chưa được tạo.

Mở **https://app.cloudfailover.id.vn** trên browser: dashboard dùng AWS, không lỗi TLS. Không dùng `--resolve` ở bước này vì đang kiểm tra DNS routing thật.

### Quy tắc chọn record với hai check đã gắn

| AWS check | Azure check | DNS authoritative chọn |
|---|---|---|
| Healthy | Healthy hoặc Unhealthy | Primary AWS |
| Unhealthy | Healthy | Secondary Azure |
| Unhealthy | Unhealthy | Primary AWS; không có origin khỏe để cứu request |

Khi AWS healthy lại, DNS quay về primary (failback); cache của client có thể còn Azure một lúc. Bỏ health check secondary khiến Route 53 coi secondary có thể dùng dù nó lỗi, nên bài này gắn cả hai. [AWS: cách chọn record](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-how-route-53-chooses-records.html).

## 7. Thử failover — stop app AWS, giữ Azure hoạt động

Chuẩn bị ba cửa sổ: **PowerShell laptop** để probe, **SSH EC2** để stop/start app, **AWS Console** để xem checks. Không chạy probe trên EC2 sắp bị gây lỗi.

### 7.1. LAPTOP — bắt đầu probe shared domain

Mở tab PowerShell riêng trên laptop, copy cả cụm:

```powershell
while ($true) {
    $ProbeAt = (Get-Date).ToUniversalTime().ToString("o")
    try {
        $Result = Invoke-RestMethod -Uri "https://app.cloudfailover.id.vn/version" -TimeoutSec 5
        Write-Host "$ProbeAt OK environment=$($Result.environment) version=$($Result.version)"
    } catch {
        Write-Host "$ProbeAt FAIL $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 1
}
```

Ban đầu thấy nhiều dòng `aws-primary`. Dừng probe bằng **Ctrl+C** khi thử xong. Probe chờ một giây **sau khi request kết thúc**, nên request timeout làm khoảng cách lớn hơn một giây. Đây là quan sát demo, không phải phép đo tải/latency chính xác GD8.

### 7.2. CHỈ EC2 — gây lỗi app AWS

Trong SSH EC2:

```bash
cd /opt/dcs29
date -u +%FT%TZ
sudo docker compose stop app
```

Ghi giờ nếu muốn đo. Không stop Azure, không dừng NGINX, không sửa record thủ công. Trên AWS Console đợi **AWS check Unhealthy**, **Azure check Healthy**.

### 7.3. LAPTOP — đối chiếu DNS và kết quả probe

Trong tab PowerShell thứ hai, chạy từng lệnh:

```powershell
Resolve-DnsName app.cloudfailover.id.vn -Type CNAME -Server ns-1139.awsdns-14.org -DnsOnly
Resolve-DnsName app.cloudfailover.id.vn -Type A -Server 1.1.1.1
curl.exe --fail-with-body --connect-timeout 5 --max-time 10 https://app.cloudfailover.id.vn/version
curl.exe --fail-with-body https://azure-origin.cloudfailover.id.vn/health/ready
```

Kỳ vọng sau health detection: authoritative CNAME chuyển sang `azure-origin.cloudfailover.id.vn`, sau cache DNS client chuyển IP tới `172.198.68.77`, probe/curl trả `azure-standby`. Certificate vẫn hợp lệ cho `app`; version/dataset vẫn khớp AWS.

Nếu authoritative đã là Azure nhưng client còn lỗi AWS, chờ TTL/cache, thử request curl mới và đối chiếu resolver; chưa kết luận failover hỏng. Trong lượt đo không flush DNS vì sẽ thay hành vi cache cần quan sát. Có thể ghi nhận 10 response Azure thành công liên tiếp để xác nhận đã ổn định.

## 8. CHỈ EC2 khôi phục — LAPTOP kiểm tra failback

Sau khi quan sát Azure phục vụ thành công, **SSH EC2**:

```bash
cd /opt/dcs29
sudo docker compose up -d --no-deps --pull never app
sudo docker compose ps
```

**LAPTOP:**

```powershell
curl.exe --fail-with-body https://aws-origin.cloudfailover.id.vn/health/ready
```

Đợi AWS check healthy trên Console. Sau đó **LAPTOP**:

```powershell
Resolve-DnsName app.cloudfailover.id.vn -Type CNAME -Server ns-1139.awsdns-14.org -DnsOnly
curl.exe --fail-with-body https://app.cloudfailover.id.vn/version
```

DNS authoritative quay về AWS; curl/probe sau cache trả `aws-primary`. Dừng probe **Ctrl+C**, xác nhận hai origin/health checks đều healthy trước khi kết thúc.

GD6 mới thử **app process failure**. GD8 sẽ stop **toàn EC2** để chứng minh failover khi môi trường AWS không hoạt động, với monitoring/load generator nằm ngoài AWS.

## 9. Lỗi thường gặp và cách phân biệt

| Triệu chứng | Kiểm tra tại đâu / cách xử lý |
|---|---|
| DNS origin đúng nhưng curl failed to connect | Console/Portal: VM Running, IP đúng, 443 mở; SSH máy lỗi: Compose ps/logs |
| Route 53 check Unhealthy ngay từ đầu | AWS Console check details: hostname/path/443/SNI; laptop HTTPS readiness; firewall cho checker |
| AWS app dừng nhưng AWS check vẫn healthy | Check có đúng AWS origin, đang enabled, Invert tắt; đợi checker cập nhật. Không trỏ check vào `app` |
| TLS curl lỗi nhưng check healthy | Trở lại GD5, certificate/SAN/expiry/chain; checker HTTPS không thay browser TLS verification |
| Không thấy dropdown health check | Đúng AWS account, check đã tạo; refresh danh sách. Không chọn Hosted zone ID thay Health check ID |
| Tạo record báo conflict | AWS zone: record `app` cũ/simple hoặc A/AAAA xung đột CNAME; kiểm tra và lưu config trước khi thay |
| Domain chung luôn AWS dù check AWS lỗi | Primary gắn nhầm/thiếu check; Azure cũng unhealthy; authoritative hay resolver cache? |
| Domain chung trả Azure ngay bình thường | Primary unhealthy hoặc hai check gắn đảo; kiểm tra role/value/ID trong zone |
| Authoritative đã Azure nhưng laptop còn AWS | Recursive cache/OS/browser/connection reuse; TTL không buộc mọi client đổi ngay |
| Failback chưa thấy ngay | AWS readiness và health check đã healthy chưa, rồi mới xét cache |
| Shared domain HTTPS mismatch | NGINX/certificate VM đang được chọn chưa chứa `app.cloudfailover.id.vn`; sửa GD5 |

Không giảm threshold hoặc disable check để làm nó có vẻ healthy. Không đoán failover mất đúng interval × threshold: có nhiều health checker và nhiều lớp cache. Nếu cần số đo, ghi thời điểm stop, lỗi đầu tiên, check unhealthy, DNS đổi, response Azure đầu tiên và 10 response ổn định; GD8 sẽ chuẩn hóa phép đo.

## 10. Khi nghỉ, rollback và evidence tùy chọn

### 10.1. Nghỉ sau GD6

Có thể Stop EC2 và Stop/deallocate Azure khi không demo. Lúc đó ứng dụng không truy cập được; health checks sẽ unhealthy, đây là kết quả dự kiến. Route 53/health check và disk/IP vẫn có thể phát sinh phí dù VM tắt.

Bật lại cả hai trước buổi làm tiếp, kiểm tra origin readiness và đợi hai checks healthy. DNS sẽ tự chọn AWS khi primary khỏe; không phải tạo lại record/certificate. Không xóa zone/IP/certificate để nghỉ tạm.

### 10.2. Rollback chỉ khi cấu hình failover lỗi

1. Khôi phục app AWS nếu đã stop bằng mục 8; giữ Azure hoạt động.
2. Xác nhận HTTPS AWS origin pass trước khi đưa người dùng về AWS.
3. Nếu cần tạm bỏ failover: lưu cấu hình hai record `app` rồi xóa **đúng hai CNAME failover `app`**, tạo một CNAME **Simple** cùng tên `app`, Value `aws-origin.cloudfailover.id.vn`, TTL 30. Không xóa hai A origin, NS hoặc SOA.
4. Kiểm tra DNS và HTTPS domain chung. Simple record không còn chuyển cloud tự động; muốn thử lại phải khôi phục cặp failover và gắn đúng checks.

Chỉ sửa record ở AWS Console; không rollback HTTPS GD5 hoặc đổi nameserver Mắt Bão khi lỗi chỉ nằm ở routing GD6. Health check sai thì sửa target/path/SNI; không dùng `Disable` như cách chặn traffic vì semantics của check disabled khác endpoint lỗi.

### 10.3. Evidence cho báo cáo — có thể bỏ qua ở lượt hướng dẫn

Theo lựa chọn hiện tại của bạn, không bắt buộc lưu file evidence để ứng dụng chạy. Khi cần báo cáo, ghi trên laptop vào `experiments/`:

- Hai Health check IDs, cấu hình và screenshot healthy/unhealthy.
- Hai failover records, TTL/role/check và bộ NS đang dùng.
- DNS trước/trong/sau sự cố; response environment AWS → Azure → AWS.
- Timeline đo, version/dataset giống nhau, phí tài nguyên thực tế.

Không lưu access key/secret/private key. Dùng screenshot Console không chứa credential.

## 11. Checklist nghiệm thu GD6

- [ ] Hai VM Running, HTTPS origin và shared hostname đều pass GD5.
- [ ] Hai health check riêng origin: HTTPS/443, `/health/ready`, SNI bật, 30 giây/threshold 3.
- [ ] Stop app từng origin làm đúng check unhealthy; đã khôi phục cả hai.
- [ ] Hai CNAME `app` cùng tên/type/routing, đúng Primary AWS và Secondary Azure, gắn đúng checks.
- [ ] Bình thường DNS authoritative và `/version` shared domain chọn AWS.
- [ ] AWS app lỗi, Azure healthy: DNS chọn Azure và shared HTTPS phục vụ `azure-standby`.
- [ ] Khôi phục AWS: health healthy, DNS/probe quay về `aws-primary` sau cache.
- [ ] Không bỏ TLS verify; version/commit/dataset giữ nguyên giữa hai cloud.
- [ ] Kết thúc thử: cả hai apps/checks healthy, probe đã dừng.
- [ ] Nếu cần báo cáo: lưu evidence/timeline/cost (tùy chọn ở lượt này).

## 12. Bước tiếp theo

GD6 hoàn thành khi DNS failover/failback đã quan sát thực tế theo checklist, không chỉ khi tạo record thành công. Tiếp tục [GD7 — observability](GIAI_DOAN_07_OBSERVABILITY.md), rồi [GD8 — kiểm thử và số đo](GIAI_DOAN_08_KIEM_THU_FAILOVER.md). Chưa hứa một thời gian chuyển traffic cố định; GD8 mới đo và báo cáo với client/cache cụ thể.
