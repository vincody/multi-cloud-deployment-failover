# Giai đoạn 7 — Monitoring và observability độc lập

**Đầu vào:** GĐ5/GĐ6 đã pass với domain của bạn. Trước khi bật stack, thay **các target URL trong `observability/prometheus.yml`** (ba dòng của job `blackbox_https`, hai dòng của job `blackbox_writer`) bằng hostname của bạn theo [bảng thông số](THONG_SO_TRIEN_KHAI.md); file trong repo chỉ là cấu hình của một lượt lab, không phải giá trị mặc định để dùng nguyên.
**Mục tiêu:** thu bằng chứng availability/latency từ laptop bên ngoài AWS để monitoring vẫn sống khi EC2 bị dừng.

> **Nếu đã cài watcher:** trước stop/reboot VM, thử lỗi pod riêng hoặc rejoin, tạo `/opt/dcs29/watcher.pause` trên VM Azure (nếu đã cài watcher bằng nhánh Terraform). Khi khởi động lại phải phê duyệt startup guard sau xác nhận writer; không chờ K3s tự bật. Chỉ xóa file pause khi baseline khỏe và đã xóa marker incident cũ nếu có. Nếu không pause, việc nghỉ hoặc thử lỗi app kéo dài có thể bị watcher coi là sự cố và Stop EC2/promote Azure.

> **Ghi chú ngay lúc này:** Bạn có thể để **cả EC2 và Azure VM tắt** trong lúc nghỉ. Bộ monitoring ở GD7 chạy trên **laptop Windows**, độc lập với hai VM. Khi hai VM tắt, cả ba probe sẽ báo lỗi; đó là kết quả đúng, không phải cấu hình hỏng. Chỉ bật hai VM ở bước 7.6 trước khi xác nhận hệ thống healthy và thử failover. Route 53 health checks/hosted zone vẫn tồn tại và có thể tiếp tục phát sinh phí dù máy đã tắt. Docker Desktop/Prometheus/Grafana trên laptop chỉ ghi số liệu khi laptop và Docker đang chạy.

> **Khi không cần monitoring:** Trên **PowerShell laptop** (không SSH vào EC2/Azure), đứng tại thư mục repo rồi dừng cả Prometheus, Grafana và Blackbox Exporter bằng các lệnh sau. Chỉ cần làm nếu bạn đã chạy GD7 và tạo `observability/.env`; nếu chưa khởi chạy ba container thì không có gì cần dừng.

```powershell
Set-Location 'PATH_TO_REPO'
docker compose --env-file observability/.env -f observability/compose.yaml stop
docker compose --env-file observability/.env -f observability/compose.yaml ps --all
```

> Lệnh `stop` giữ container và dữ liệu Prometheus/Grafana. Trong lúc dừng sẽ không có mẫu monitoring mới; Route 53 và hai VM không bị ảnh hưởng. Có thể đóng Docker Desktop sau đó. Khi cần dùng lại, mở Docker Desktop rồi chạy `docker compose --env-file observability/.env -f observability/compose.yaml up -d` từ cùng thư mục. **Không dùng `down -v`** nếu muốn giữ các volume dữ liệu.

**Cập nhật Issues/K3s:** giữ các bước Docker Desktop, Prometheus/Grafana và tạo panel dưới đây. Docker Compose ở chương này chạy trên **laptop monitor**, không trên hai VM app. HTTP liveness, database writer readiness và pod state là ba tín hiệu khác nhau. /health/database Azure standby khỏe trả 503, không dùng nó làm probe sống của Azure.

Trước mục 7.3, sửa các URL target trong observability/prometheus.yml (ba của `blackbox_https`, hai của `blackbox_writer`) sang hostname thật; `blackbox_https` dùng /health/live để đo process/TLS, `blackbox_writer` dùng /health/database để thấy origin nào đang nhận ghi. Liveness 200 không chứng minh DB/schema/ghi hoạt động; thêm kiểm tra DB/Issues riêng ở 7.11.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Theo dõi endpoint, pod, VM và database từ laptop bên ngoài hai cloud. |
| Chuẩn bị | Docker Desktop; hostname thật; SSH vào cả hai VM cho exporter. |
| Hoàn tất khi | Grafana nhận đủ probe, pod/scale, VM và database metrics. |

**Luồng thao tác:** [Hướng dẫn thực hiện từ đầu đến cuối](#hướng-dẫn-thực-hiện-từ-đầu-đến-cuối) → [7.11. Bổ sung giám sát pod, scale và database — từng bước](#711-bổ-sung-giám-sát-pod-scale-và-database--từng-bước) → [7.12. VM và database metrics — cấu hình hoàn chỉnh cho demo](#712-vm-và-database-metrics--cấu-hình-hoàn-chỉnh-cho-demo) → [Điều kiện hoàn thành GD7](#điều-kiện-hoàn-thành-gd7).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 0. Bản đồ thao tác — đọc trước khi làm

| Nhãn trong hướng dẫn | Thực hiện ở đâu |
|---|---|
| **LAPTOP / POWERSHELL** | Terminal PowerShell Windows mở tại thư mục repo của bạn, **không SSH** |
| **LAPTOP / TRÌNH DUYỆT** | Docker Desktop, Prometheus `http://localhost:9090`, Grafana `http://localhost:3000` |
| **AWS CONSOLE** | EC2 → Instances → Start/kiểm tra EC2 primary |
| **AZURE PORTAL** | Virtual machines → Start/kiểm tra Azure standby |
| **CHỈ EC2** | Lệnh Linux chạy trong phiên SSH vào AWS, chỉ dùng ở bước thử lỗi AWS |
| **SSH CẢ HAI VM** | Phê duyệt startup guard khi reboot; cài kube-state-metrics, node exporter và DB collector ở 7.11–7.12 |

Không cài Prometheus/Grafana lên EC2 hoặc Azure VM. Laptop là điểm quan sát thứ ba: EC2 lỗi thì số liệu vẫn ghi nếu laptop còn hoạt động. Khi laptop tắt/sleep hoặc Internet laptop mất, xuất hiện khoảng trống dữ liệu; **không diễn giải khoảng trống đó thành downtime ứng dụng**.

Ba URL cần cấu hình cho lần chạy mới:

| Nhãn | URL probe | IP origin đã dùng ở GD5/GD6 |
|---|---|---|
| `aws-origin` | `https://aws-origin.YOUR_DOMAIN/health/live` | `AWS_PUBLIC_IP` |
| `azure-origin` | `https://azure-origin.YOUR_DOMAIN/health/live` | `AZURE_PUBLIC_IP` |
| `shared-domain` | `https://app.YOUR_DOMAIN/health/live` | Route 53 chọn AWS hoặc Azure |

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
if (-not (Test-Path observability/.env)) { Copy-Item observability/.env.example observability/.env }
notepad observability/.env
notepad observability/prometheus.yml
```

Trong `.env`, thay giá trị sau dấu `=` bằng mật khẩu riêng, lưu và đóng. Không thêm dấu nháy hoặc khoảng trắng ở hai đầu. File `.env` đã được `.gitignore`; không commit hoặc gửi lên chat. Nếu đã có `.env`, bỏ qua `Copy-Item` để tránh ghi đè mật khẩu.

Trong `prometheus.yml`, tìm **ba dòng `targets` dưới job `blackbox_https`** và thay URL thành `https://aws-origin.YOUR_DOMAIN/health/live`, `https://azure-origin.YOUR_DOMAIN/health/live`, `https://app.YOUR_DOMAIN/health/live` sau khi thay `YOUR_DOMAIN` bằng domain thật. Làm tương tự với **hai dòng `targets` dưới job `blackbox_writer`**: `https://aws-origin.YOUR_DOMAIN/health/database` và `https://azure-origin.YOUR_DOMAIN/health/database`. Giữ nguyên label `target_name` tương ứng. Lưu file, đọc lại các URL để chắc chắn không còn hostname/IP của người triển khai khác. Chỉ sau bước này mới chạy `docker compose ... up -d` ở 7.3.

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

- `observability/blackbox.yml`: GET HTTPS `/health/live`, chỉ chấp nhận HTTP `200`, kiểm tra TLS certificate thật, dùng IPv4, timeout 7 giây. **Không dùng `curl -k` hoặc `insecure_skip_verify`**.
- `observability/prometheus.yml`: mỗi 10 giây gọi Blackbox `/probe` cho ba URL `/health/live` (job `blackbox_https`) và hai URL `/health/database` (job `blackbox_writer`), timeout scrape 8 giây. Ở job `blackbox_writer`, `probe_success=1` chỉ có ở origin đang là writer; standby luôn là 0 và đó là trạng thái đúng. Nhãn `target_name` giữ tên dễ đọc. `prometheus` tự scrape chính nó.
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

Đến bước này mới cần bật cloud. Nếu lượt trước đã failover sang Azure, không Start AWS với primary cũ tự khởi động: chặn workload/endpoint và rejoin theo GĐ6 trước. Các bước bật máy bên dưới dành cho trạng thái ổn định đã xác nhận chỉ một writer:

1. **AWS CONSOLE:** EC2 → Instances → chọn EC2 GD3 → **Start instance**. Đợi trạng thái `Running` và status checks pass. Elastic IP dự kiến vẫn là `AWS_PUBLIC_IP`.
2. **AZURE PORTAL:** Virtual machines → chọn VM GD4 → **Start**. Đợi `Running`. Static Public IP dự kiến vẫn là `AZURE_PUBLIC_IP`.
3. **SSH CẢ HAI VM:** chỉ trong lần restart bình thường đã xác nhận AWS primary/Azure standby trước khi tắt và chưa có failover, phê duyệt guard rồi kiểm tra:

   ```bash
   sudo touch /run/issues-k3s-start-approved
   sudo systemctl reset-failed k3s
   sudo systemctl start k3s
   sudo k3s kubectl get nodes,pods -A
   ```

   Nếu không xác định writer hoặc đã promote Azure, giữ AWS K3s dừng và dùng runbook rejoin GĐ6; không touch marker để ép app lên. Sau start xác nhận role/streaming qua API.
4. **LAPTOP / POWERSHELL:** chạy **từng lệnh**:

   ```powershell
   Resolve-DnsName aws-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   Resolve-DnsName azure-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
   curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/health/live
   curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
   ```

Hai DNS origin phải trả đúng IP trên; ba lệnh liveness phải thành công. `/version` ở trạng thái bình thường dự kiến `environment=aws`. Nếu sau Start VM mà `curl` chưa kết nối, xác nhận startup guard đã được phê duyệt đúng role rồi đợi K3s/Traefik/app khởi động; kiểm tra bước khởi động trong GD5/GD6 trước khi sửa monitoring. Nếu IP đã thay đổi thì sửa bản ghi A origin và cập nhật tài liệu, không cố dùng IP cũ.

### 7.7. LAPTOP — xác nhận probe và Grafana

1. Quay lại Prometheus → **Query** → `probe_success{job="blackbox_https"}` → **Execute**. Cả ba nhãn `target_name` phải về `1` sau vài chu kỳ; đợi thêm nếu VM mới khởi động. `probe_http_status_code` phải là `200` cho cả ba.
2. Query `probe_duration_seconds{job="blackbox_https"}`: mỗi dòng có độ trễ probe tính bằng giây. Query `probe_ssl_earliest_cert_expiry{job="blackbox_https"}`: nếu có dòng, đây là Unix timestamp hết hạn cert sớm nhất được quan sát. Dùng biểu thức `(probe_ssl_earliest_cert_expiry{job="blackbox_https"} - time()) / 86400` để xem số ngày còn lại. Nếu metric không xuất hiện, xem log probe và TLS trước khi kết luận certificate đã hết hạn.
3. Mở `http://localhost:3000` → đăng nhập username `admin`, password bạn đã đặt ở 7.2. Nếu Grafana hỏi đổi mật khẩu thì đặt mật khẩu mới và ghi nhớ; thay đổi này nằm trong volume Grafana. Lần sau sửa `.env` **không tự đổi** mật khẩu của user đã tạo trong volume.
4. Menu **Connections → Data sources** (hoặc **Configuration → Data sources** tùy phiên bản) → chọn **Prometheus**. URL phải là `http://prometheus:9090`; datasource này được tạo sẵn từ file provisioning và có thể ở chế độ chỉ đọc. Nếu có nút **Save & test/Test**, bấm thử. Nếu không có, mở **Explore**, chọn datasource **Prometheus**, chạy `probe_success{job="blackbox_https"}` để xác nhận có dữ liệu. Không tạo datasource thứ hai trùng tên.

### 7.8. LAPTOP / GRAFANA — tạo dashboard theo từng panel

> Repo đã có sẵn dashboard `DCS29 - Failover` (provisioning trong `observability/grafana/dashboards/`): panel Writer cho thấy origin nào nhận ghi, hai panel còn lại là health và thời gian phản hồi. Các bước tạo panel thủ công bên dưới chỉ cần khi muốn thêm panel riêng.

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
4. Để đánh dấu lần test: trong dashboard, tạo **Annotation** tại thời điểm bạn dừng/khôi phục app (tùy phiên bản Grafana: Ctrl+click vào biểu đồ, hoặc menu annotation của dashboard). Nội dung ngắn, ví dụ `t_action stop AWS app`, `t_detect Route53 unhealthy`, `t_first_B first Azure response after promotion`, `t_stable shared healthy`, kèm UTC. Đây là mốc thao tác thủ công, không suy ra từ riêng đồ thị 10 giây.
5. Export dashboard JSON qua menu **Share/Export → Export as JSON** hoặc **Dashboard settings → JSON model** (vị trí tùy phiên bản), lưu trong `observability/grafana/dashboards/` (thư mục này được Grafana tự nạp, đã có `dcs29-failover.json`) nếu muốn version-control. Kiểm tra JSON không chứa password/token trước khi commit. Screenshot chỉ là hình minh họa; JSON + dữ liệu thô mới đủ tái kiểm tra.

**Lưu ý về app metrics:** `/metrics` trong FastAPI có `dcs29_http_requests_total` và `dcs29_http_request_duration_seconds`. Cấu hình GD7 **chưa scrape hai endpoint này** để không phụ thuộc vào việc mở metrics qua Internet. Do đó panel request rate và latency **bên trong app** chưa có dữ liệu. Đừng tạo panel rồi diễn giải `No data` thành ứng dụng không có request. Nếu cần các panel này ở giai đoạn sau, thiết kế đường truy cập bảo vệ `/metrics` (ví dụ VPN/SSH tunnel hoặc allowlist ổn định) rồi mới thêm scrape job; không thêm `https://.../metrics` công khai chỉ cho tiện demo. Hãy kiểm tra ingress Traefik hiện tại: nếu URL `/metrics` đang được truy cập từ Internet, endpoint đó đang public từ GD5 và nên được hạn chế trước khi triển khai thật.

### 7.9. Smoke test pod — trên AWS K3s, không promote Azure

Khi app Deployment đã chạy, ghi UTC từ PowerShell và mở Grafana. Trên SSH AWS:

```bash
sudo k3s kubectl -n issues get pods -l app=issues-app
sudo k3s kubectl -n issues rollout restart deployment/issues-app
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
sudo k3s kubectl -n issues get pods -l app=issues-app
```

Quan sát pod cũ thay bằng pod mới và HTTP probe có thể gián đoạn ngắn nếu chỉ một replica. Đây là K3s hồi phục app, không phải failover cloud/DB. Không stop PostgreSQL hoặc promote Azure trong bài này.

Nếu muốn quan sát một vòng cloud failover, thực hiện đủ fencing/WAL/promote/rejoin/failback theo GĐ6 rồi ghi timeline ở Grafana. Không chỉ restart AWS và để DNS tự failback về DB cũ. Khi pod exporter/cluster không truy cập được, phân biệt mất scrape với pod Ready=0.

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

## 7.11. Bổ sung giám sát pod, scale và database — từng bước

### A. Triển khai kube-state-metrics trong từng cluster

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

### B. Đưa metrics tới laptop qua kênh quản trị

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

### C. Thêm scrape jobs và kiểm tra từ container monitor

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

### D. Grafana panel pod/replica

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

Grafana cập nhật sau scrape; kiểm tra CPU/RAM và cùng image/DSN. Sau thử giảm lại replicas=1 bằng `sudo k3s kubectl -n issues scale deployment/issues-app --replicas=1`, chờ rollout và kiểm tra panel. Hồi phục pod không bảo đảm tất cả giao dịch thành công, vẫn đối chiếu API/DB.

### E. Database và VM metrics

Mục 7.12 bên dưới cung cấp node exporter và collector PostgreSQL qua SSH tunnel, theo dõi disk, CPU/RAM, role, streaming và WAL lag bytes. /api/database/status hiện cho biết role/LSN nhưng không phải endpoint Prometheus; cần exporter/collector để chuyển thành metric, không thêm nó vào scrape_configs rồi mong tự parse JSON.

Ghi CPU credit từ cloud metrics khi tích hợp. Prometheus/Grafana không tự fence/promote PostgreSQL; GĐ6B có watcher tùy chọn; Prometheus/Grafana vẫn chỉ quan sát. Hoàn tất GĐ7 khi pod fail/recover/scale và tín hiệu DB được kiểm chứng, không chỉ ba probe HTTP.

## 7.12. VM và database metrics — cấu hình hoàn chỉnh cho demo

Các bước này làm sau schema/app hoạt động. Không cần thêm database exporter có password: script trên VM dùng admin Unix socket trong pod, chỉ xuất số liệu không chứa credential. Node exporter đọc file Prometheus textfile; JSON /api/database/status không được scrape trực tiếp.

### A. Node exporter trên mỗi VM

Chọn/pin image linux/amd64 từ `quay.io/prometheus/node-exporter`; thay NODE_EXPORTER_IMAGE_DIGEST trong YAML bằng digest thực. Tạo thư mục textfile trên mỗi SSH:

```bash
sudo install -d -m 755 /var/lib/dcs29-metrics
nano /opt/dcs29/node-exporter.yaml
```

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-exporter
  namespace: kube-system
spec:
  selector:
    matchLabels: {app: node-exporter}
  template:
    metadata:
      labels: {app: node-exporter}
    spec:
      hostNetwork: true
      containers:
        - name: exporter
          image: NODE_EXPORTER_IMAGE_DIGEST
          args:
            - --web.listen-address=127.0.0.1:9100
            - --path.procfs=/host/proc
            - --path.sysfs=/host/sys
            - --path.rootfs=/host/root
            - --collector.textfile.directory=/textfile
          resources:
            requests: {cpu: 25m, memory: 32Mi}
            limits: {cpu: 200m, memory: 128Mi}
          volumeMounts:
            - {name: proc, mountPath: /host/proc, readOnly: true}
            - {name: sys, mountPath: /host/sys, readOnly: true}
            - {name: root, mountPath: /host/root, readOnly: true, mountPropagation: HostToContainer}
            - {name: textfile, mountPath: /textfile, readOnly: true}
      volumes:
        - name: proc
          hostPath: {path: /proc}
        - name: sys
          hostPath: {path: /sys}
        - name: root
          hostPath: {path: /}
        - name: textfile
          hostPath: {path: /var/lib/dcs29-metrics, type: Directory}
```

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/node-exporter.yaml
sudo k3s kubectl apply -f /opt/dcs29/node-exporter.yaml
sudo k3s kubectl -n kube-system rollout status daemonset/node-exporter --timeout=120s
curl -fsS http://127.0.0.1:9100/metrics
```

Không mở 9100 trên SG/NSG. Mẫu bind loopback và mount root read-only để thu metric host; đây là workload quản trị tin cậy, không cấp quyền tương tự cho app.

### B. Xuất role, writer gate, streaming và lag bytes

Trên SSH từng VM, mở `sudo nano /usr/local/bin/dcs29-db-metrics`, dán script:

```bash
#!/bin/bash
set -euo pipefail
OUT_DIR=/var/lib/dcs29-metrics
install -d -m 755 "$OUT_DIR"
TMP_FILE=$(mktemp "$OUT_DIR/.db.XXXXXX")
trap 'rm -f "$TMP_FILE"' EXIT
if VALUES=$(/usr/local/bin/k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -At -c "SELECT concat('issues_db_standby ',pg_is_in_recovery()::int,chr(10),'issues_db_writable ',((NOT pg_is_in_recovery()) AND (SELECT writes_enabled FROM app_control WHERE singleton=true))::int,chr(10),'issues_db_streaming ',(CASE WHEN pg_is_in_recovery() THEN EXISTS(SELECT 1 FROM pg_stat_wal_receiver WHERE status='streaming') ELSE EXISTS(SELECT 1 FROM pg_stat_replication WHERE state='streaming') END)::int,chr(10),'issues_db_replay_lag_bytes ',CASE WHEN pg_is_in_recovery() THEN COALESCE(pg_wal_lsn_diff(pg_last_wal_receive_lsn(),pg_last_wal_replay_lsn()),0) ELSE COALESCE((SELECT max(pg_wal_lsn_diff(pg_current_wal_flush_lsn(),replay_lsn)) FROM pg_stat_replication),0) END);" 2>/dev/null); then
  printf 'issues_db_collect_success 1\n%s\n' "$VALUES" > "$TMP_FILE"
else
  printf 'issues_db_collect_success 0\n' > "$TMP_FILE"
fi
printf 'issues_db_sample_timestamp_seconds %s\n' "$(date +%s)" >> "$TMP_FILE"
chmod 644 "$TMP_FILE"
mv "$TMP_FILE" "$OUT_DIR/database.prom"
```

Lag standby là received−replayed, không tính WAL chưa nhận; phía primary là flush−standby replay. Nếu streaming=0 thì lag=0 không chứng minh đồng bộ, phải xem cả hai metric. Đây là byte lag, không phải giây/RPO. Collector failure không thay thế writer health check.

```bash
sudo chmod 750 /usr/local/bin/dcs29-db-metrics
sudo /usr/local/bin/dcs29-db-metrics
sudo cat /var/lib/dcs29-metrics/database.prom
sudo nano /etc/systemd/system/dcs29-db-metrics.service
```

Nội dung service:

```ini
[Unit]
Description=DCS29 PostgreSQL metrics
After=k3s.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/dcs29-db-metrics
TimeoutStartSec=20
```

Tạo `/etc/systemd/system/dcs29-db-metrics.timer`:

```ini
[Unit]
Description=Collect DCS29 database metrics
[Timer]
OnBootSec=30
OnUnitActiveSec=10
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dcs29-db-metrics.timer
sudo systemctl list-timers dcs29-db-metrics.timer
curl -fsS http://127.0.0.1:9100/metrics | rg issues_db
```

Nếu rg chưa có trên Ubuntu, dùng grep issues_db cho lệnh cuối. Collector không start K3s khi startup guard đang chặn; trong rejoin collect_success=0 là bình thường.

### C. Tunnel từ laptop và Prometheus targets

PowerShell laptop, mỗi SSH tunnel giữ một terminal riêng:

```powershell
ssh -N -i PATH_TO_AWS_KEY -L 127.0.0.1:19091:127.0.0.1:9100 ubuntu@AWS_PUBLIC_IP
```

```powershell
ssh -N -i PATH_TO_AZURE_KEY -L 127.0.0.1:19092:127.0.0.1:9100 AZURE_ADMIN_USER@AZURE_PUBLIC_IP
```

Trong observability/prometheus.yml, thêm cùng cấp dưới scrape_configs:

```yaml
  - job_name: node_aws
    static_configs:
      - targets: ['host.docker.internal:19091']
        labels: {cluster: aws}
  - job_name: node_azure
    static_configs:
      - targets: ['host.docker.internal:19092']
        labels: {cluster: azure}
```

```powershell
docker compose --env-file observability/.env -f observability/compose.yaml exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose --env-file observability/.env -f observability/compose.yaml restart prometheus
```

Kiểm tra Targets UP **từ container**; Docker Desktop phải truy cập được host listener. Nếu không được, không đổi listener thành 0.0.0.0; kiểm tra hỗ trợ host networking của Docker Desktop và cấu hình mạng monitor tách biệt trước demo.

Grafana tạo panel theo cách ở 7.8:

| Panel | PromQL |
|---|---|
| RAM dùng (%) | `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` |
| CPU dùng (%) | `100 * (1 - avg by(cluster)(rate(node_cpu_seconds_total{mode="idle"}[2m])))` |
| Đĩa root trống | `node_filesystem_avail_bytes{mountpoint="/"}` |
| Standby | `issues_db_standby` |
| Writer | `issues_db_writable` |
| Streaming | `issues_db_streaming` |
| WAL lag bytes | `issues_db_replay_lag_bytes` |
| Collector hoạt động | `issues_db_collect_success` |
| Mẫu đã cũ (giây) | `time() - issues_db_sample_timestamp_seconds` |

Trạng thái bình thường: AWS writer=1/standby=0, Azure writer=0/standby=1, streaming=1 cả hai. Khi Stop VM, scrape UP=0; không diễn giải metric cũ là VM vẫn khỏe. CPU credit của T3/B2s cần xem thêm AWS CloudWatch/Azure Metrics trong Console, không có trong node exporter.

Nguồn: [Node exporter và textfile collector](https://github.com/prometheus/node_exporter). Chưa xác nhận cấu hình trên VM thật; kiểm chứng scrape, restart, failover và số liệu trước báo cáo.
## Điều kiện hoàn thành GD7

- [ ] Bộ ba container chạy trên laptop, Prometheus và Grafana mở được qua localhost.
- [ ] Khi hai VM bật, cả ba target có probe_success = 1 và HTTP status = 200.
- [ ] Grafana có dashboard health, status, duration, tỷ lệ lỗi, TLS expiry; biết giải thích từng panel.
- [ ] Thử rollout restart app K3s, theo dõi probe và pod; không promote DB chỉ vì pod lỗi.
- [ ] Hồi phục pod và xác nhận probe, DB role và replication; cloud failback theo GĐ6.
- [ ] Ghi mốc UTC, phiên bản image, interval 10 giây, retention 7 ngày; export dashboard JSON và raw data khi làm buổi đo chính thức.
- [ ] Pod/replica và VM/database metrics ở 7.11–7.12 đã scrape được từ container Prometheus.
- [ ] Kiểm tra tình trạng public của /metrics trước khi định thêm scrape app metrics.

Cấu hình chuẩn bị xong chưa có nghĩa GD7 đã hoàn thành: cần kiểm chứng 7.1–7.9 cho HTTP/TLS và 7.11–7.12 cho pods/replicas/VM/database. Sau đó dùng dashboard/timeline này ở [GD8 — kiểm thử failover](GIAI_DOAN_08_KIEM_THU_FAILOVER.md).

Nguồn kỹ thuật: [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [Prometheus multi-target exporter](https://prometheus.io/docs/guides/multi-target-exporter/), [Blackbox exporter configuration](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).


---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## Giải thích tín hiệu và giới hạn phép đo

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

| Tín hiệu | Ý nghĩa đúng |
|---|---|
| Prometheus target UP | Prometheus liên lạc được với Blackbox exporter; website đích có thể vẫn lỗi |
| probe_success = 1 | URL đích trả HTTP 200, HTTPS và certificate hợp lệ trong thời gian timeout |
| probe_success = 0 | URL đích không đạt điều kiện trên; xem status, log probe, DNS/TLS và trạng thái VM |
| shared-domain healthy | Người dùng mới có thể tới một origin healthy; không xác định được AWS hay Azure chỉ từ metric này |
| Khoảng trắng trên biểu đồ | Có thể do laptop/Docker/Prometheus ngừng chạy hoặc mất Internet; không tự coi là downtime website |

Khoảng lấy mẫu 10 giây không thể xác định thời điểm sự kiện chính xác tới từng giây. Route 53 có chu kỳ health check và DNS cache riêng; so sánh các timestamp ở GD8 với sai số lấy mẫu và cache. Các probe không thay thế được log client hoặc phép đo của `scripts/probe_failover.py`. Monitor trên laptop không phải dịch vụ giám sát 24/7.

</details>

## Khi gặp lỗi

<details>
<summary>Khi gặp lỗi — mở hướng dẫn</summary>

| Hiện tượng | Kiểm tra theo thứ tự |
|---|---|
| docker info không kết nối | Mở Docker Desktop, đợi Engine running, kiểm tra Linux containers/WSL 2 |
| Grafana localhost:3000 không mở | docker compose ps, logs grafana; cổng 3000 có bị ứng dụng khác dùng không |
| Prometheus target blackbox DOWN | Container blackbox, log Prometheus/Blackbox, file cấu hình và mạng Docker |
| Target UP nhưng probe_success = 0 | Đây là lỗi đích; kiểm tra hai VM có Running, curl HTTPS trên laptop, DNS, TLS, Traefik/app |
| AWS probe 0, Azure 1, shared 0 lâu | Kiểm tra Route 53 health check, record failover và DNS cache theo GD6 |
| HTTP status = 0 | Không nhận được HTTP response; xem DNS/TCP/TLS/timeout, không gọi đó là mã lỗi của app |
| SSL expiry không có dữ liệu | Xem probe có đi qua HTTPS thành công và metric exporter có được phát hay không |
| Dashboard No data | Kiểm tra query, time range, datasource và xem Prometheus có mẫu cùng khoảng thời gian |
| Sau nghỉ thấy gap | Kiểm tra laptop/Docker có dừng, VM có tắt; ghi rõ khoảng ngừng thu mẫu |

Nếu cần xem chi tiết lỗi một probe, mở trên laptop qua Prometheus UI để xem sample, hoặc dùng lệnh sau rồi tìm log của target. Blackbox không mở cổng trực tiếp ra host:

~~~powershell
docker compose --env-file observability/.env -f observability/compose.yaml logs --tail 100 blackbox
~~~

</details>
