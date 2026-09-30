# Giai đoạn 7 — Monitoring và observability độc lập

**Đầu vào:** GĐ5/GĐ6 đã pass với domain của bạn. Trước khi bật stack, thay **ba target URL trong `observability/prometheus.yml`** bằng hostname của bạn theo [bảng thông số](THONG_SO_TRIEN_KHAI.md); file trong repo chỉ là cấu hình của một lượt lab, không phải giá trị mặc định để dùng nguyên.
**Mục tiêu:** thu bằng chứng availability/latency từ laptop bên ngoài AWS để monitoring vẫn sống khi EC2 bị dừng.

> **Ghi chú ngay lúc này:** Bạn có thể để **cả EC2 và Azure VM tắt** trong lúc nghỉ. Bộ monitoring ở GD7 chạy trên **laptop Windows**, độc lập với hai VM. Khi hai VM tắt, cả ba probe sẽ báo lỗi; đó là kết quả đúng, không phải cấu hình hỏng. Chỉ bật hai VM ở bước 7.6 trước khi xác nhận hệ thống healthy và thử failover. Route 53 health checks/hosted zone vẫn tồn tại và có thể tiếp tục phát sinh phí dù máy đã tắt. Docker Desktop/Prometheus/Grafana trên laptop chỉ ghi số liệu khi laptop và Docker đang chạy.

> **Khi không cần monitoring:** Trên **PowerShell laptop** (không SSH vào EC2/Azure), đứng tại thư mục repo rồi dừng cả Prometheus, Grafana và Blackbox Exporter bằng các lệnh sau. Chỉ cần làm nếu bạn đã chạy GD7 và tạo `observability/.env`; nếu chưa khởi chạy ba container thì không có gì cần dừng.

```powershell
Set-Location 'PATH_TO_REPO'
docker compose --env-file observability/.env -f observability/compose.yaml stop
docker compose --env-file observability/.env -f observability/compose.yaml ps --all
```

> Lệnh `stop` giữ container và dữ liệu Prometheus/Grafana. Trong lúc dừng sẽ không có mẫu monitoring mới; Route 53 và hai VM không bị ảnh hưởng. Có thể đóng Docker Desktop sau đó. Khi cần dùng lại, mở Docker Desktop rồi chạy `docker compose --env-file observability/.env -f observability/compose.yaml up -d` từ cùng thư mục. **Không dùng `down -v`** nếu muốn giữ các volume dữ liệu.

## 0. Bản đồ thao tác — đọc trước khi làm

| Nhãn trong hướng dẫn | Thực hiện ở đâu |
|---|---|
| **LAPTOP / POWERSHELL** | Terminal PowerShell Windows mở tại thư mục repo của bạn, **không SSH** |
| **LAPTOP / TRÌNH DUYỆT** | Docker Desktop, Prometheus `http://localhost:9090`, Grafana `http://localhost:3000` |
| **AWS CONSOLE** | EC2 → Instances → Start/kiểm tra EC2 primary |
| **AZURE PORTAL** | Virtual machines → Start/kiểm tra Azure standby |
| **CHỈ EC2** | Lệnh Linux chạy trong phiên SSH vào AWS, chỉ dùng ở bước thử lỗi AWS |
| **CHỈ AZURE** | Không cần SSH để cài GD7; chỉ SSH nếu Azure không tự chạy lại app |

Không cài Prometheus/Grafana lên EC2 hoặc Azure VM. Laptop là điểm quan sát thứ ba: EC2 lỗi thì số liệu vẫn ghi nếu laptop còn hoạt động. Khi laptop tắt/sleep hoặc Internet laptop mất, xuất hiện khoảng trống dữ liệu; **không diễn giải khoảng trống đó thành downtime ứng dụng**.

Ba URL cần cấu hình cho lần chạy mới:

| Nhãn | URL probe | IP origin đã dùng ở GD5/GD6 |
|---|---|---|
| `aws-origin` | `https://aws-origin.YOUR_DOMAIN/health/ready` | `AWS_PUBLIC_IP` |
| `azure-origin` | `https://azure-origin.YOUR_DOMAIN/health/ready` | `AZURE_PUBLIC_IP` |
| `shared-domain` | `https://app.YOUR_DOMAIN/health/ready` | Route 53 chọn AWS hoặc Azure |

**Quan trọng:** `aws-origin` và `azure-origin` là hostname riêng, không đi qua record failover `app`. Target `shared-domain` chỉ cho biết URL người dùng đang hoạt động; nó **không cho biết cloud nào đã trả lời**. Để xác định cloud, gọi `/version` và đọc `environment` như GD6/GD8.

## Hướng dẫn thực hiện từ đầu đến cuối

### 7.1. LAPTOP — kiểm tra Docker Desktop và vị trí repo

1. Mở **Docker Desktop** trên Windows. Đợi góc dưới/trạng thái hiện **Engine running**; chọn Linux containers nếu Docker đang ở Windows containers. Nếu chưa cài, làm theo [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) và bật WSL 2 theo hướng dẫn chính thức. Không cần cài Docker trên hai VM thêm lần nữa.
2. Mở **PowerShell mới** trên laptop; chạy từng lệnh:

   ```powershell
   Set-Location 'PATH_TO_REPO'
   docker --version
   docker compose version
   docker info --format '{{.ServerVersion}}'
   ```

   Lệnh cuối phải in phiên bản Docker Engine. Nếu báo `failed to connect to the docker API`, Docker Desktop chưa chạy xong; mở app và đợi rồi chạy lại. Các lệnh từ 7.1 đến 7.8 đều ở **PowerShell laptop**, trừ nơi có nhãn khác.

### 7.2. LAPTOP — tạo mật khẩu Grafana local

Repo có sẵn `observability/compose.yaml`, `prometheus.yml`, `blackbox.yml` và Grafana datasource. **Không cần tạo lại YAML**, nhưng **phải sửa URL probe** vì file mẫu còn hostname của lượt triển khai trước. Trên PowerShell laptop:

```powershell
Copy-Item observability/.env.example observability/.env
notepad observability/.env
notepad observability/prometheus.yml
```

Trong `.env`, thay giá trị sau dấu `=` bằng mật khẩu riêng, lưu và đóng. Không thêm dấu nháy hoặc khoảng trắng ở hai đầu. File `.env` đã được `.gitignore`; không commit hoặc gửi lên chat. Nếu đã có `.env`, bỏ qua `Copy-Item` để tránh ghi đè mật khẩu.

Trong `prometheus.yml`, tìm **ba dòng `targets` dưới job `blackbox_https`** và thay URL thành `https://aws-origin.YOUR_DOMAIN/health/ready`, `https://azure-origin.YOUR_DOMAIN/health/ready`, `https://app.YOUR_DOMAIN/health/ready` sau khi thay `YOUR_DOMAIN` bằng domain thật. Giữ nguyên label `target_name` tương ứng. Lưu file, đọc lại ba URL để chắc chắn không còn hostname/IP của người triển khai khác. Chỉ sau bước này mới chạy `docker compose ... up -d` ở 7.3.

### 7.3. LAPTOP — bật stack monitoring

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

### 7.4. LAPTOP — hiểu ba file cấu hình trước khi xem số liệu

- `observability/blackbox.yml`: GET HTTPS `/health/ready`, chỉ chấp nhận HTTP `200`, kiểm tra TLS certificate thật, dùng IPv4, timeout 7 giây. **Không dùng `curl -k` hoặc `insecure_skip_verify`**.
- `observability/prometheus.yml`: mỗi 10 giây gọi Blackbox `/probe` cho ba URL, timeout scrape 8 giây. Nhãn `target_name` giữ tên dễ đọc. `prometheus` tự scrape chính nó.
- `observability/compose.yaml`: retention 7 ngày; Grafana đọc Prometheus qua mạng Docker nội bộ `http://prometheus:9090`, nên trong Grafana **không nhập `localhost:9090`** làm datasource.

Xem log nếu container vừa khởi động xong mà UI chưa lên:

```powershell
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 prometheus
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 blackbox
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 50 grafana
```

### 7.5. LAPTOP — kiểm tra Prometheus khi hai VM **vẫn đang tắt**

1. Trình duyệt laptop → `http://localhost:9090/targets`. `prometheus` cần **UP**. Ba dòng thuộc job `blackbox_https` thường cũng **UP** vì Prometheus gọi được **exporter**; trạng thái này **không chứng minh website healthy**.
2. Trình duyệt laptop → `http://localhost:9090/graph` (hoặc **Query**). Dán `probe_success{job="blackbox_https"}` vào ô query → **Execute** → chọn **Table**. Khi cả hai VM tắt, ba target dự kiến là `0`. Có thể thấy `0` sau tối đa vài chu kỳ 10 giây. Nếu không có dòng nào, kiểm tra container/log/network.
3. Thử thêm `probe_http_status_code{job="blackbox_https"}`. Khi không kết nối được, mã có thể là `0`; đừng hiểu đó là HTTP status do server trả về.

**Giải thích:** `up{job="blackbox_https"}=1` nghĩa là scrape từ Blackbox exporter thành công. `probe_success=1` mới nghĩa là endpoint đích trả đúng `200`, qua HTTPS với TLS hợp lệ. Nếu hai VM đang tắt thì `probe_success=0` là kết quả đúng.

### 7.6. AWS CONSOLE + AZURE PORTAL — bật lại đúng hai origin

Đến bước này mới cần bật cloud:

1. **AWS CONSOLE:** EC2 → Instances → chọn EC2 GD3 → **Start instance**. Đợi trạng thái `Running` và status checks pass. Elastic IP dự kiến vẫn là `AWS_PUBLIC_IP`.
2. **AZURE PORTAL:** Virtual machines → chọn VM GD4 → **Start**. Đợi `Running`. Static Public IP dự kiến vẫn là `AZURE_PUBLIC_IP`.
3. **LAPTOP / POWERSHELL:** chạy **từng lệnh**:

   ```powershell
   Resolve-DnsName aws-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   Resolve-DnsName azure-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/health/ready
   curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/ready
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/health/ready
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
   ```

Hai DNS origin phải trả đúng IP trên; ba lệnh readiness phải thành công. `/version` ở trạng thái bình thường dự kiến `environment=aws-primary`. Nếu sau Start VM mà `curl` chưa kết nối, đợi vài phút cho Docker/NGINX/app trên VM khởi động; kiểm tra bước khởi động trong GD5/GD6 trước khi sửa monitoring. Nếu IP đã thay đổi thì sửa bản ghi A origin và cập nhật tài liệu, không cố dùng IP cũ.

### 7.7. LAPTOP — xác nhận probe và Grafana

1. Quay lại Prometheus → **Query** → `probe_success{job="blackbox_https"}` → **Execute**. Cả ba nhãn `target_name` phải về `1` sau vài chu kỳ; đợi thêm nếu VM mới khởi động. `probe_http_status_code` phải là `200` cho cả ba.
2. Query `probe_duration_seconds{job="blackbox_https"}`: mỗi dòng có độ trễ probe tính bằng giây. Query `probe_ssl_earliest_cert_expiry{job="blackbox_https"}`: nếu có dòng, đây là Unix timestamp hết hạn cert sớm nhất được quan sát. Dùng biểu thức `(probe_ssl_earliest_cert_expiry{job="blackbox_https"} - time()) / 86400` để xem số ngày còn lại. Nếu metric không xuất hiện, xem log probe và TLS trước khi kết luận certificate đã hết hạn.
3. Mở `http://localhost:3000` → đăng nhập username `admin`, password bạn đã đặt ở 7.2. Nếu Grafana hỏi đổi mật khẩu thì đặt mật khẩu mới và ghi nhớ; thay đổi này nằm trong volume Grafana. Lần sau sửa `.env` **không tự đổi** mật khẩu của user đã tạo trong volume.
4. Menu **Connections → Data sources** (hoặc **Configuration → Data sources** tùy phiên bản) → chọn **Prometheus**. URL phải là `http://prometheus:9090`; datasource này được tạo sẵn từ file provisioning và có thể ở chế độ chỉ đọc. Nếu có nút **Save & test/Test**, bấm thử. Nếu không có, mở **Explore**, chọn datasource **Prometheus**, chạy `probe_success{job="blackbox_https"}` để xác nhận có dữ liệu. Không tạo datasource thứ hai trùng tên.

### 7.8. LAPTOP / GRAFANA — tạo dashboard theo từng panel

1. Grafana → **Dashboards → New → New dashboard → Add visualization** → chọn datasource **Prometheus**.
2. Tạo từng panel từ bảng dưới. Trong editor panel, chọn loại hiển thị; nhập PromQL ở ô query; đổi **Title**; **Apply/Back to dashboard**; lặp lại. Với panel có ba series, đặt legend `{{target_name}}` để phân biệt AWS/Azure/domain. Chọn time range `Last 30 minutes` và refresh `10s` ở góc dashboard. UI có thể thay đổi tên nút giữa các phiên bản.

   | Panel / loại | PromQL | Cách đọc |
   |---|---|---|
   | **Health ba target** / Time series hoặc State timeline | `probe_success{job="blackbox_https"}` | `1` healthy, `0` lỗi; legend `{{target_name}}` |
   | **HTTP status** / Time series | `probe_http_status_code{job="blackbox_https"}` | `200` thành công; `502` app lỗi qua NGINX; `0` thường là chưa nhận HTTP response |
   | **Độ trễ probe** / Time series | `probe_duration_seconds{job="blackbox_https"}` | Giây cho toàn bộ probe từ laptop; không phải latency riêng trong FastAPI |
   | **Tỷ lệ probe lỗi 5 phút** / Time series | `1 - avg_over_time(probe_success{job="blackbox_https"}[5m])` | Tỷ lệ `0–1`; đặt Unit = Percent (0.0–1.0) |
   | **TLS còn hạn** / Time series hoặc Stat | `(probe_ssl_earliest_cert_expiry{job="blackbox_https"} - time()) / 86400` | Ngày còn lại; đặt Unit = days |

3. Bấm **Save dashboard**, đặt tên ví dụ `DCS29 - Multi-cloud external health`. Chọn timezone **UTC** trong dashboard settings nếu UI có, hoặc ghi rõ timezone đang hiển thị trong báo cáo. Mốc đo ở GD8 phải ghi UTC.
4. Để đánh dấu lần test: trong dashboard, tạo **Annotation** tại thời điểm bạn dừng/khôi phục app (tùy phiên bản Grafana: Ctrl+click vào biểu đồ, hoặc menu annotation của dashboard). Nội dung ngắn, ví dụ `t_action stop AWS app`, `t_detect Route53 unhealthy`, `t_first_B first azure-standby`, `t_stable shared healthy`, kèm UTC. Đây là mốc thao tác thủ công, không suy ra từ riêng đồ thị 10 giây.
5. Export dashboard JSON qua menu **Share/Export → Export as JSON** hoặc **Dashboard settings → JSON model** (vị trí tùy phiên bản), lưu trong `observability/grafana/dcs29-dashboard.json` nếu muốn version-control. Kiểm tra JSON không chứa password/token trước khi commit. Screenshot chỉ là hình minh họa; JSON + dữ liệu thô mới đủ tái kiểm tra.

**Lưu ý về app metrics:** `/metrics` trong FastAPI có `dcs29_http_requests_total` và `dcs29_http_request_duration_seconds`. Cấu hình GD7 **chưa scrape hai endpoint này** để không phụ thuộc vào việc mở metrics qua Internet. Do đó panel request rate và latency **bên trong app** chưa có dữ liệu. Đừng tạo panel rồi diễn giải `No data` thành ứng dụng không có request. Nếu cần các panel này ở giai đoạn sau, thiết kế đường truy cập bảo vệ `/metrics` (ví dụ VPN/SSH tunnel hoặc allowlist ổn định) rồi mới thêm scrape job; không thêm `https://.../metrics` công khai chỉ cho tiện demo. Hãy kiểm tra NGINX hiện tại: nếu URL `/metrics` đang được truy cập từ Internet, endpoint đó đang public từ GD5 và nên được hạn chế trước khi triển khai thật.

### 7.9. LAPTOP + CHỈ EC2 — smoke test có kiểm soát (làm khi cả hai VM đang chạy)

Mục này kiểm tra monitoring ghi lại biến cố. **Không chạy nếu Azure đang tắt.** GD6 đã chứng minh failover; ở đây chỉ cần thử ngắn và quan sát đồ thị. Trước khi dừng app, **LAPTOP** query `probe_success` và xác nhận AWS/Azure/shared đều `1`.

1. **CHỈ EC2, trong SSH AWS** (không chạy trên Azure):

   ```bash
   cd /opt/dcs29
   sudo docker compose stop app
   ```

   Nếu service trong Compose của bạn không tên `app`, xem `sudo docker compose config --services` rồi dùng đúng tên service app; không stop NGINX/Certbot/Azure. Ghi thời gian UTC `t_action` từ PowerShell laptop: `(Get-Date).ToUniversalTime().ToString('o')`.
2. **LAPTOP:** quan sát Prometheus/Grafana. AWS `probe_success` phải xuống `0`; Azure vẫn `1`. Shared có thể xuống `0` trong thời gian Route 53 phát hiện lỗi/cache DNS, rồi trở lại `1`. Đây là probe độc lập, **không đồng nghĩa browser cũ sẽ tự đổi kết nối**. Dùng `curl.exe --fail-with-body https://app.YOUR_DOMAIN/version` hoặc browser mới để xác nhận `azure-standby` khi DNS đã failover. Nhớ Route 53 health check của GD6 chạy khoảng 30 giây và cần nhiều kết quả liên tiếp; đừng kết luận sau một probe 10 giây.
3. **CHỈ EC2:** khôi phục ngay sau khi đã quan sát:

   ```bash
   cd /opt/dcs29
   sudo docker compose up -d --no-deps --pull never app
   sudo docker compose ps
   ```

4. **LAPTOP:** xác nhận AWS probe trở lại `1`, Route 53 eventually ưu tiên AWS và `/version` trên shared trở lại `aws-primary`. Nếu browser cũ vẫn hiển thị 502/standby, đóng tab/browser rồi thử trình duyệt mới như đã gặp ở GD6. Ghi `t_recover` UTC. Không cố ép DNS bằng `--resolve` khi đo đường người dùng.

### 7.10. LAPTOP — lưu dữ liệu, nghỉ và làm tiếp hôm sau

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

## Giải thích tín hiệu và giới hạn phép đo

| Tín hiệu | Ý nghĩa đúng |
|---|---|
| Prometheus target UP | Prometheus liên lạc được với Blackbox exporter; website đích có thể vẫn lỗi |
| probe_success = 1 | URL đích trả HTTP 200, HTTPS và certificate hợp lệ trong thời gian timeout |
| probe_success = 0 | URL đích không đạt điều kiện trên; xem status, log probe, DNS/TLS và trạng thái VM |
| shared-domain healthy | Người dùng mới có thể tới một origin healthy; không xác định được AWS hay Azure chỉ từ metric này |
| Khoảng trắng trên biểu đồ | Có thể do laptop/Docker/Prometheus ngừng chạy hoặc mất Internet; không tự coi là downtime website |

Khoảng lấy mẫu 10 giây không thể xác định thời điểm sự kiện chính xác tới từng giây. Route 53 có chu kỳ health check và DNS cache riêng; so sánh các timestamp ở GD8 với sai số lấy mẫu và cache. Ba probe không thay thế được log client hoặc phép đo k6. Monitor trên laptop không phải dịch vụ giám sát 24/7.

## Khi gặp lỗi

| Hiện tượng | Kiểm tra theo thứ tự |
|---|---|
| docker info không kết nối | Mở Docker Desktop, đợi Engine running, kiểm tra Linux containers/WSL 2 |
| Grafana localhost:3000 không mở | docker compose ps, logs grafana; cổng 3000 có bị ứng dụng khác dùng không |
| Prometheus target blackbox DOWN | Container blackbox, log Prometheus/Blackbox, file cấu hình và mạng Docker |
| Target UP nhưng probe_success = 0 | Đây là lỗi đích; kiểm tra hai VM có Running, curl HTTPS trên laptop, DNS, TLS, NGINX/app |
| AWS probe 0, Azure 1, shared 0 lâu | Kiểm tra Route 53 health check, record failover và DNS cache theo GD6 |
| HTTP status = 0 | Không nhận được HTTP response; xem DNS/TCP/TLS/timeout, không gọi đó là mã lỗi của app |
| SSL expiry không có dữ liệu | Xem probe có đi qua HTTPS thành công và metric exporter có được phát hay không |
| Dashboard No data | Kiểm tra query, time range, datasource và xem Prometheus có mẫu cùng khoảng thời gian |
| Sau nghỉ thấy gap | Kiểm tra laptop/Docker có dừng, VM có tắt; ghi rõ khoảng ngừng thu mẫu |

Nếu cần xem chi tiết lỗi một probe, mở trên laptop qua Prometheus UI để xem sample, hoặc dùng lệnh sau rồi tìm log của target. Blackbox không mở cổng trực tiếp ra host:

~~~powershell
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 100 blackbox
~~~

## Điều kiện hoàn thành GD7

- [ ] Bộ ba container chạy trên laptop, Prometheus và Grafana mở được qua localhost.
- [ ] Khi hai VM bật, cả ba target có probe_success = 1 và HTTP status = 200.
- [ ] Grafana có dashboard health, status, duration, tỷ lệ lỗi, TLS expiry; biết giải thích từng panel.
- [ ] Thử dừng app AWS: AWS xuống 0, Azure giữ 1, shared phục hồi 1; Grafana vẫn tiếp tục ghi.
- [ ] Khôi phục app AWS và xác nhận cả ba target trở lại 1.
- [ ] Ghi mốc UTC, phiên bản image, interval 10 giây, retention 7 ngày; export dashboard JSON và raw data khi làm buổi đo chính thức.
- [ ] Kiểm tra tình trạng public của /metrics trước khi định thêm scrape app metrics.

Cấu hình chuẩn bị xong chưa có nghĩa GD7 đã hoàn thành: bạn cần chạy các bước 7.1–7.9 khi có thời gian. Sau đó dùng dashboard/timeline này ở [GD8 — kiểm thử failover](GIAI_DOAN_08_KIEM_THU_FAILOVER.md).

Nguồn kỹ thuật: [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [Prometheus multi-target exporter](https://prometheus.io/docs/guides/multi-target-exporter/), [Blackbox exporter configuration](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
