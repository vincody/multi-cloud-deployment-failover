# Đề tài #29 - Multi-Cloud Deployment & Failover

## 1. Mục tiêu đề tài

Xây dựng và vận hành cùng một ứng dụng trên **hai môi trường độc lập**. Khi một môi trường gặp sự cố hoàn toàn, hệ thống phát hiện lỗi và chuyển traffic sang môi trường còn lại để giảm downtime.

Đề tài cần trả lời bằng thực nghiệm:

1. Failover mất bao lâu?
2. Trong lúc failover có bao nhiêu request lỗi hoặc bị gián đoạn?
3. Latency trước, trong và sau failover thay đổi thế nào?
4. Việc có hai môi trường tăng độ sẵn sàng nhưng đổi lại bao nhiêu chi phí và độ phức tạp vận hành?

Theo yêu cầu trong danh sách môn học, sản phẩm phải có: triển khai ứng dụng ở hai môi trường, health monitoring, traffic switching/failover, mô phỏng một môi trường chết hoàn toàn, đo failover time/downtime/latency/cost proxy, và nêu trade-off vận hành.

## 2. Bản chất: High Availability, không chỉ là “dùng nhiều cloud”

`Multi-cloud` trong phạm vi đồ án không nhất thiết là AWS + Azure + GCP thật. Có thể mô phỏng bằng hai môi trường tách biệt trên cùng một máy hoặc hai máy khác nhau:

```text
                         +------------------+
Client / k6 / Browser -->| Load balancer    |
                         | Health checking  |
                         +--------+---------+
                                  |
                 +----------------+----------------+
                 |                                 |
                 v                                 v
        Environment A / Primary             Environment B / Standby
        API + dependencies                  API + dependencies
        database/mock data                  database/mock data
```

Nếu A hỏng, load balancer ngừng gửi request đến A và chuyển sang B. Đây là **failover**.

Điểm khó nhất không phải chạy hai bản API, mà là **dữ liệu**: nếu người dùng vừa ghi dữ liệu vào A trước khi A chết, B có biết dữ liệu đó không? Vì vậy cần chọn phạm vi dữ liệu ngay từ đầu.

## 3. Phạm vi khuyến nghị: Active-Passive cho ứng dụng stateless

Đây là hướng nhóm nên chọn nếu muốn vừa đủ ấn tượng vừa giảm rủi ro.

### Ý tưởng sản phẩm

Một website đơn giản, ví dụ:

- Tra cứu lịch học / lịch sự kiện / danh mục sản phẩm.
- Đặt lịch khám hoặc đặt chỗ với dữ liệu đơn giản.
- Dashboard xem trạng thái thiết bị/phòng lab.

Không cần làm nghiệp vụ phức tạp. Ứng dụng chỉ là “đối tượng” để chứng minh hệ thống vẫn phục vụ khi môi trường chính chết.

### Kiến trúc khuyến nghị

```text
                         +-----------------------+
                         | Client / k6 / Locust   |
                         +-----------+-----------+
                                     |
                                     v
                         +-----------------------+
                         | NGINX / HAProxy        |
                         | - active health check  |
                         | - route A or B         |
                         +-----+-------------+---+
                               |             |
              healthy traffic  |             |  failover traffic
                               v             v
                 +------------------+  +------------------+
                 | Environment A    |  | Environment B    |
                 | API replicas     |  | API replicas     |
                 | Redis/cache opt. |  | Redis/cache opt. |
                 +--------+---------+  +--------+---------+
                          |                      |
                          +----------+-----------+
                                     v
                    Shared read-only data / mock data

Prometheus <---- metrics from LB, A, B ----> Grafana
```

### Vì sao là `active-passive`

- A là môi trường nhận traffic chính.
- B luôn chạy, health check bình thường nhưng không hoặc ít nhận traffic.
- Khi A hỏng, traffic chuyển sang B.
- Dễ chứng minh failover, ít khó hơn active-active.

### Dữ liệu ở scope an toàn

Ưu tiên một trong ba cách dưới đây:

| Cách | Độ khó | Khi dùng |
| --- | ---: | --- |
| Dữ liệu chỉ đọc | Thấp | Demo tra cứu, catalogue, dashboard |
| Mock/in-memory data giống nhau ở A và B | Thấp | Muốn tập trung hoàn toàn vào network/failover |
| PostgreSQL primary-replica một chiều | Trung bình | Muốn chứng minh RPO và dữ liệu sau recovery |

**Không nên** làm database ghi hai chiều ở A và B. Khi một bên mất mạng, việc giải quyết conflict/consistency có thể vượt quá thời gian đồ án.

## 4. Ba hướng phát triển

## Hướng A - Mô phỏng local bằng Docker Compose (khuyến nghị)

### Phù hợp khi

- Nhóm mới với DevOps hoặc Kubernetes.
- Muốn môi trường lặp lại được, miễn phí và dễ demo.
- Máy có khoảng 16 GB RAM trở lên là thoải mái; 8 GB vẫn làm được nếu stack gọn.

### Các thành phần cần deploy

| Thành phần | Số lượng | Vai trò |
| --- | ---: | --- |
| NGINX hoặc HAProxy | 1 | Reverse proxy, health check, failover traffic |
| API environment A | 1-2 containers | Primary application |
| API environment B | 1-2 containers | Standby application |
| PostgreSQL/Mock data | 1-2 containers | Dữ liệu theo scope đã chọn |
| Prometheus | 1 | Thu metric |
| Grafana | 1 | Dashboard |
| k6 hoặc Locust | 1 hoặc chạy từ host | Tạo traffic liên tục để đo failover |

### Cách mô phỏng hai môi trường độc lập

- Tạo hai Docker Compose project hoặc hai Docker network: `env-a` và `env-b`.
- Mỗi môi trường có tên service, subnet, biến môi trường và log riêng.
- Load balancer nằm ngoài hai network hoặc kết nối được cả hai network.
- Khi demo sự cố, dừng toàn bộ compose project của A, không chỉ dừng một API container.

### Điểm mạnh/yếu

- Điểm mạnh: đơn giản, chi phí gần như bằng 0, dễ viết script dựng/xóa/chạy lại.
- Điểm yếu: chưa phải hai cloud/cluster vật lý thật; cần nêu rõ đây là **multi-environment emulation** trong báo cáo.

## Hướng B - Hai Kubernetes cluster local

### Phù hợp khi

- Nhóm đã biết Kubernetes cơ bản.
- Muốn thể hiện Helm/manifest, Deployment, Service, Ingress, monitoring.

### Các thành phần cần deploy

```text
Cluster A: namespace app-a -> Deployment API -> Service -> Ingress
Cluster B: namespace app-b -> Deployment API -> Service -> Ingress
Outside clusters: global NGINX/HAProxy -> route đến ingress A hoặc B
Monitoring: Prometheus/Grafana tập trung hoặc mỗi cluster một Prometheus
```

- Dùng Kind hoặc Minikube để tạo hai cluster.
- Triển khai cùng một image/version vào hai cluster bằng Helm chart hoặc Kustomize.
- Global load balancer health-check endpoint `/health` của mỗi cluster.
- Có thể thêm GitHub Actions để build image và deploy manifest tự động.

### Điểm mạnh/yếu

- Điểm mạnh: rất đúng chất Cloud-DevOps, dễ trình bày IaC/deployment consistency.
- Điểm yếu: tốn RAM/CPU, networking giữa hai cluster khó hơn Docker Compose, dễ mất thời gian cấu hình.

## Hướng C - Hai cloud thật hoặc một cloud + local

### Phù hợp khi

- Nhóm đã có tài khoản cloud/credit, hiểu billing và network.
- Có thời gian quản lý quyền, firewall, DNS, public endpoint và chi phí.

### Ví dụ

- Environment A: Google Cloud Run hoặc VM.
- Environment B: AWS ECS/Lambda/EC2 hoặc local public tunnel.
- DNS/load balancer bên ngoài theo dõi health endpoint và chuyển traffic.

### Điểm mạnh/yếu

- Điểm mạnh: tính thực tế cao nhất.
- Điểm yếu: dễ phát sinh chi phí và vấn đề tài khoản; không cần thiết để chứng minh tiêu chí môn học.

**Khuyến nghị:** chỉ dùng hướng này sau khi bản Docker Compose đã chạy ổn định.

## 5. Các mode failover nên hiểu

| Mode | Cách hoạt động | Độ khó | Ghi chú |
| --- | --- | ---: | --- |
| Active-passive | A phục vụ chính, B chờ sẵn | Trung bình | Lựa chọn tốt nhất cho đồ án |
| Active-active | A và B cùng nhận traffic | Cao | Cần phân phối traffic và đồng bộ dữ liệu tốt |
| Manual failover | Người vận hành chuyển route | Thấp | Có thể làm baseline, nhưng không đủ là mục tiêu cuối |
| Automatic failover | Health check tự đổi route | Trung bình | Nên là bản chính để đáp ứng đề tài |

## 6. Những gì cần deploy và cấu hình

## 6.1 Ứng dụng

Ứng dụng tối thiểu cần các endpoint:

```text
GET /health        -> 200 nếu service sẵn sàng phục vụ
GET /api/resource  -> API để client/k6 gọi liên tục
GET /metrics       -> Prometheus scrape metric (hoặc exporter)
GET /version       -> trả environment A/B và version để chứng minh route đang đi đâu
```

`/version` rất hữu ích khi demo: người xem sẽ thấy request ban đầu trả `environment: A`, sau khi failover trả `environment: B`.

## 6.2 Load balancer / traffic manager

Nên dùng **HAProxy** nếu muốn health check và log backend rõ ràng; dùng **NGINX** nếu nhóm quen reverse proxy hơn.

Cần cấu hình:

- Hai backend A và B.
- Health check định kỳ đến `/health`.
- Ngưỡng xác định backend down, ví dụ 3 lần check liên tiếp thất bại.
- Ưu tiên traffic cho A khi A healthy.
- Route traffic về B khi A unhealthy.
- Access log có timestamp, upstream backend, status code và response time.

## 6.3 Monitoring

Prometheus nên thu:

- `up` của API A và B.
- Request rate, error rate và duration của API.
- CPU/memory container/node nếu có exporter.
- Số request theo backend A/B từ load balancer.

Grafana cần ít nhất ba dashboard/panel:

1. **Availability:** A/B đang up hay down.
2. **Traffic:** request đang đi A hay B.
3. **User impact:** latency, error rate, throughput trong lúc failover.

## 6.4 Log và trace (mở rộng)

- Thu access log của load balancer và application log vào Loki/OpenSearch.
- Gắn `request_id` để nối log client -> load balancer -> API.
- OpenTelemetry trace là phần nâng cao, không cần cho bản tối thiểu.

## 6.5 Data layer

### Mức cơ bản: Stateless / read-only

- App đọc một catalogue JSON/SQLite read-only giống nhau ở A và B.
- Không có rủi ro mất dữ liệu khi failover.
- Phù hợp để tập trung vào HA và routing.

### Mức tốt: Primary-replica database một chiều

```text
API A -> PostgreSQL primary -- streaming/logical replication --> PostgreSQL replica <- API B
```

- Khi A chết, B đọc từ replica.
- Nêu rõ dữ liệu mới nhất có thể chưa replicate hết: đây là RPO.
- Promote replica sang primary chỉ làm nếu nhóm đã kiểm thử cẩn thận.

### Không khuyến nghị: Dual-write/active-active database

- Cần giải quyết write conflict, split-brain và consistency.
- Scope dễ chuyển thành đề tài distributed database thay vì failover.

## 7. Kế hoạch triển khai theo giai đoạn

| Giai đoạn | Kết quả cần đạt |
| --- | --- |
| 1. Chốt scope | Chọn active-passive, loại dữ liệu và kiến trúc Docker/Kubernetes |
| 2. Xây app | API có `/health`, `/version`, `/metrics`; Dockerfile chạy được local |
| 3. Dựng A và B | Cùng image/version, config/environment riêng, chạy độc lập |
| 4. Route cơ bản | Load balancer gửi traffic cố định đến A; B có thể truy cập trực tiếp để kiểm tra |
| 5. Health check/failover | Tắt A và xác nhận traffic tự chuyển sang B |
| 6. Quan sát | Prometheus/Grafana và log; hiển thị A down, B nhận traffic |
| 7. Benchmark | Chạy k6/Locust liên tục, lặp lại sự cố, thu dữ liệu |
| 8. Mở rộng có chọn lọc | Database replica, CI/CD, auto-failback hoặc chaos test nhẹ |
| 9. Hoàn thiện | README chạy lại được, diagram, report, demo video |

## 8. Kịch bản thí nghiệm bắt buộc

Mỗi kịch bản nên chạy ít nhất 3 lần để lấy trung bình, ghi chính xác timestamp và cấu hình.

| Kịch bản | Hành động | Chỉ số cần thu |
| --- | --- | --- |
| Baseline | A healthy, chỉ route đến A | latency, throughput, error rate |
| Manual failover | Đổi route thủ công sang B | thời gian chuyển route, request lỗi |
| Automatic failover | Tắt toàn bộ A trong lúc k6 gửi request liên tục | detection time, failover time, downtime, request lỗi |
| Recovery/failback | Khôi phục A | thời gian A healthy lại, behavior traffic |
| Load + failure | Duy trì tải bình thường/cao rồi tắt A | latency tail p95/p99, throughput, error rate |
| Data scenario (nếu có DB) | Ghi dữ liệu rồi tắt A | dữ liệu còn ở B, RPO, consistency |

### Định nghĩa chỉ số

- **Detection time:** từ lúc A chết đến lúc load balancer đánh dấu A unhealthy.
- **Failover time:** từ lúc A chết đến request thành công đầu tiên ở B.
- **Downtime:** khoảng thời gian client không nhận được response thành công.
- **Error rate:** số request lỗi / tổng request trong một cửa sổ thời gian.
- **Latency:** p50, p95, p99; đừng chỉ báo trung bình.
- **Cost proxy:** số container/pod/VM hoạt động, CPU/RAM cấp phát, hoặc ước tính chi phí theo runtime.

## 9. Kịch bản demo 5-7 phút

1. Mở Grafana: A và B healthy; traffic hiện đi A.
2. Mở trang `/version` hoặc dashboard: kết quả trả từ `environment A`.
3. Chạy k6/Locust để sinh request liên tục.
4. Dừng toàn bộ Environment A.
5. Cho thấy health check nhận diện A down.
6. Cho thấy traffic chuyển sang B và `/version` trả `environment B`.
7. Hiển thị chart latency/error rate/downtime ở thời điểm failover.
8. Khởi động lại A, giải thích chiến lược failback và trade-off dữ liệu.

## 10. Các hướng mở rộng để tăng chất lượng

Chỉ thêm sau khi bản failover tối thiểu chạy ổn định.

| Hướng mở rộng | Giá trị | Độ khó |
| --- | --- | ---: |
| CI/CD bằng GitHub Actions | Build image, test, deploy đồng nhất A/B | Trung bình |
| IaC bằng Terraform/Ansible | Tái lập môi trường, giảm cấu hình tay | Trung bình |
| Helm/Kustomize | Quản lý manifest đa môi trường | Trung bình |
| PostgreSQL primary-replica | Có RPO, minh họa data recovery | Trung bình-cao |
| Auto-failback có kiểm soát | Khôi phục A mà không gây flapping | Cao |
| Alertmanager | Cảnh báo khi backend down/failover xảy ra | Thấp-trung bình |
| Chaos test nhẹ | CPU/network delay trước khi chết hoàn toàn | Trung bình-cao |
| Blue-green/canary deployment | Liên hệ với release strategy | Cao |
| Global DNS failover | Sát thực tế cloud hơn | Cao |

## 11. Phân công nhóm 4 người

| Thành viên | Trách nhiệm chính |
| --- | --- |
| 1 - Application | API, `/health`, `/version`, `/metrics`, dữ liệu demo |
| 2 - Platform | Docker Compose/Kubernetes, network A/B, load balancer |
| 3 - Observability | Prometheus, Grafana, log, alert, dashboard |
| 4 - Experiment/Report | k6/Locust, kịch bản lỗi, số liệu, chart, report/demo |

Tất cả thành viên nên nắm kiến trúc và chạy được toàn bộ project từ README.

## 12. Rủi ro lớn và cách tránh

| Rủi ro | Hậu quả | Cách tránh |
| --- | --- | --- |
| Làm active-active quá sớm | Kẹt ở data consistency | Bắt đầu active-passive, stateless/read-only |
| Hai environment dùng chung một dependency | “Failover” không còn độc lập | Tách network/config; ghi rõ dependency nào còn shared |
| Chỉ switch route thủ công | Không đáp ứng automatic failover | Có health check và backend down detection |
| Không có workload liên tục | Không đo được user impact | Dùng k6/Locust xuyên suốt sự cố |
| Chỉ báo latency trung bình | Che mất spike lúc failover | Báo p95/p99 và error rate theo thời gian |
| Quá nhiều tool | Hạ tầng không chạy được | Chốt bản tối thiểu trước: API + A/B + HAProxy/NGINX + Prometheus/Grafana + k6 |
| Không phân biệt failover và data recovery | Báo cáo thiếu trọng tâm | Nêu rõ HA, RTO/RPO và chiến lược dữ liệu |

## 13. Bộ deliverable nên nộp

- Source code ứng dụng và Dockerfile.
- `docker-compose.yml` hoặc Helm/Kubernetes manifests cho A/B.
- Cấu hình NGINX/HAProxy health check và routing.
- Cấu hình Prometheus/Grafana, dashboard export nếu có.
- Script k6/Locust và script kích hoạt failure.
- README có lệnh dựng môi trường, chạy benchmark và dừng A.
- Architecture diagram.
- Bảng/charts failover time, downtime, latency, error rate và cost proxy.
- Báo cáo trade-off: độ sẵn sàng, chi phí, độ phức tạp, dữ liệu/RPO.
- Video demo hoặc kịch bản live demo.

## 14. Chốt hướng nên làm

Nên bắt đầu bằng cấu hình sau:

> **Docker Compose, active-passive, hai environment A/B độc lập, HAProxy/NGINX automatic health check, ứng dụng stateless hoặc read-only, Prometheus/Grafana và k6.**

Sau khi chạy ổn định, chọn **một** nâng cấp có giá trị cao: PostgreSQL primary-replica, CI/CD, Alertmanager hoặc Kubernetes hai cluster. Làm sâu phần thí nghiệm/failover luôn có giá trị hơn việc thêm nhiều công cụ nhưng không đo được kết quả.
