# Phân tích đề tài theo hướng DevOps / Cloud-DevOps

Tài liệu này chỉ giữ các đề tài liên quan trực tiếp đến DevOps trong danh sách môn **Distributed Computing Systems**. Giả định nhóm có 3-4 thành viên, thời gian làm một học phần, có thể dùng Docker và lập trình backend cơ bản.

## Kết luận nhanh

| Khuyến nghị | Đề tài | Lý do |
| --- | --- | --- |
| Tốt nhất cho nhóm trung bình | **#9 - Observability & SRE Playbook** | Rất sát công việc DevOps/SRE, phạm vi kiểm soát được, demo và số liệu rõ |
| Tốt nhất để học Kubernetes | **#1 - Cloud-native Microservices with Autoscaling** | Thực hành Docker, Kubernetes, HPA, monitoring và load test |
| Tốt nhất khi nhóm thiên backend | **#23 - Circuit Breaker & Resilient Microservices** | Dễ thấy giá trị của resilience, ít nặng hạ tầng hơn Kubernetes |
| Tốt nhất về vận hành/khôi phục | **#7 - Hybrid Backup & Automated Failover** | RTO/RPO, backup, restore và failover rất thực tế |
| Chỉ nên chọn khi nền tảng mạnh | **#22 hoặc #29** | Hay nhưng dễ quá tải vì Kubernetes, network và hạ tầng nhiều môi trường |

## 1. #9 - Observability & SRE Playbook

### Bài toán và giá trị

Xây dựng hệ thống quan sát cho một cloud service/microservices: biết hệ thống đang khỏe hay lỗi, lỗi ở đâu, cảnh báo ai và xử lý theo quy trình nào. Đây là công việc DevOps/SRE cốt lõi, không chỉ là triển khai ứng dụng.

### Kiến trúc đề xuất

```text
Client -> API Gateway -> Service A -> Service B -> Database
                    |       |             |
                    +-------+-------------+--> Metrics: Prometheus
                    +------------------------> Logs: Loki
                    +------------------------> Traces: Jaeger / Tempo

Prometheus -> Alertmanager -> Thông báo cảnh báo
Grafana -> Dashboard tổng quan và dashboard sự cố
```

### Phạm vi tối thiểu

1. Dựng ứng dụng 2-3 service bằng Docker Compose hoặc Kubernetes.
2. Instrument metrics, structured logs và distributed traces.
3. Đặt 2 SLI, ví dụ availability và p95 latency; từ đó định nghĩa 1 SLO.
4. Tạo 2 alert: error rate tăng và latency vượt ngưỡng.
5. Chủ động gây lỗi: tắt service, làm database chậm hoặc tạo CPU load.
6. Viết runbook xử lý và postmortem cho một incident.

### Thí nghiệm và chỉ số

- Request rate, error rate, p50/p95/p99 latency.
- CPU, memory, số request đang xử lý, database connection.
- MTTD (thời gian phát hiện sự cố) và MTTR (thời gian khôi phục).
- So sánh khả năng phát hiện lỗi trước/sau khi có dashboard và alert.

### Công nghệ và rủi ro

Docker Compose, Prometheus, Grafana, Loki, Promtail/Fluent Bit, OpenTelemetry, Jaeger/Tempo; backend Python/FastAPI, Node.js hoặc Java. Không dựng quá nhiều công cụ cùng lúc: bản tối thiểu nên là Prometheus + Grafana + Loki, trace là phần mở rộng.

**Độ khó:** 3/5. **Tính DevOps:** 5/5. **Khả năng đạt điểm cao:** rất cao nếu thí nghiệm và postmortem tốt.

---

## 2. #1 - Cloud-native Microservices with Autoscaling

### Bài toán và điều cần hiểu đúng

Xây ứng dụng gồm nhiều microservice, đóng gói container, triển khai Kubernetes và tự tăng/giảm số pod theo tải. Đề tài này **không bắt buộc dùng AWS**; AWS EKS/GKE/AKS chỉ là nơi chạy Kubernetes. Nhóm có thể làm miễn phí bằng **Minikube** hoặc **Kind** trên máy cá nhân.

### Kiến trúc đề xuất

```text
Load generator -> Ingress / Service -> API service -> Catalogue service -> Database
                                      |               |
                                      +--> Prometheus +--> Grafana

Kubernetes HPA theo dõi CPU hoặc custom metric
             -> scale số replica của API/Catalogue service
```

### Phạm vi tối thiểu

1. Tạo API, catalogue và database.
2. Viết Dockerfile/Docker Compose để chạy local trước.
3. Viết Kubernetes Deployment, Service, ConfigMap/Secret.
4. Cấu hình metrics-server và HPA cho ít nhất một service.
5. Cài Prometheus/Grafana, sau đó dùng k6 hoặc Locust tạo tải.

### Thí nghiệm nên có

| Kịch bản | Đo gì | Phân tích cần có |
| --- | --- | --- |
| Không autoscaling | latency, error rate, CPU | Điểm nghẽn khi tải tăng |
| Có HPA | số replica, latency, throughput | HPA có giảm nghẽn không |
| Tải burst | thời gian scale-up | Khoảng trễ trước khi pod mới sẵn sàng |
| Tải giảm | thời gian scale-down | Tài nguyên có được thu hồi hợp lý không |

### Rủi ro

Lỗi phổ biến là chỉ deploy app rồi dừng. Điểm mạnh phải nằm ở biểu đồ, benchmark và phân tích autoscaling. HPA theo CPU là đủ cho bản cơ bản; không cần custom metrics nếu nhóm thiếu thời gian.

**Độ khó:** 3/5. **Tính DevOps:** 5/5. **Khả năng đạt điểm cao:** cao, nhưng cần thí nghiệm đẹp vì đề tài phổ biến.

---

## 3. #23 - Circuit Breaker & Resilient Microservices

### Bài toán và giá trị

Chứng minh một service chậm/lỗi có thể làm hỏng cả chuỗi request, rồi áp dụng timeout, retry, circuit breaker và fallback để ngăn cascading failure. Đây là giao điểm tốt giữa backend và SRE.

### Phạm vi đề xuất

```text
Client -> Order service -> Inventory service -> Payment mock
                     \-> Redis cache (fallback tùy chọn)
```

- Làm Inventory hoặc Payment chậm, trả lỗi hoặc ngắt kết nối.
- So sánh trước/sau timeout, exponential backoff, circuit breaker và fallback.
- Quan sát p95 latency, error rate, recovery time và trạng thái open/half-open/closed của circuit.

### Công nghệ và giới hạn

Docker Compose, Spring Boot + Resilience4j hoặc Python/Node.js, Prometheus, Grafana. Không cần nhiều microservice: hai service phụ thuộc và một database/mock là đủ để mô phỏng lỗi dây chuyền rõ ràng.

**Độ khó:** 3/5. **Tính DevOps:** 4/5. **Phù hợp:** nhóm biết backend nhưng muốn giảm phần Kubernetes.

---

## 4. #7 - Hybrid Backup and Automated Failover Demo

### Bài toán và giá trị

Xây quy trình backup dữ liệu, phục hồi và chuyển dịch vụ sang môi trường dự phòng khi môi trường chính gặp sự cố. Hai chỉ số trọng tâm:

- **RPO (Recovery Point Objective):** lượng dữ liệu có thể mất tối đa.
- **RTO (Recovery Time Objective):** thời gian tối đa để khôi phục dịch vụ.

### Phạm vi đề xuất

1. Chạy PostgreSQL/app chính ở local VM hoặc Docker.
2. Sao lưu theo lịch vào MinIO (mô phỏng S3) hoặc storage khác.
3. Viết script restore tự động sang môi trường dự phòng.
4. Health check phát hiện primary lỗi và chuyển traffic sang backup.
5. Đo RTO, RPO và downtime trong nhiều kịch bản.

### Thí nghiệm và rủi ro

- Lỗi ứng dụng nhưng database còn hoạt động; lỗi database; lỗi toàn bộ primary environment.
- Có thể so sánh backup đầy đủ và incremental nếu còn thời gian.
- “Hybrid” có thể mô phỏng bằng hai Docker network/VM và MinIO; phải ghi rõ đây là giả lập.

**Độ khó:** 3/5. **Tính DevOps:** 5/5. **Phù hợp:** nhóm thích automation, infrastructure và vận hành.

---

## 5. #27 - Distributed Log Aggregation System

### Bài toán và giá trị

Thu log từ nhiều service, chuyển qua tầng ingestion, lưu/index để tìm kiếm và hiển thị dashboard. Đây là phần logging chuyên sâu của observability.

### Kiến trúc và thí nghiệm

```text
Service containers -> Fluent Bit -> Kafka / buffer -> OpenSearch -> Grafana
```

- Ít nhất 3 service sinh structured JSON logs.
- So sánh gửi trực tiếp với gửi qua buffer/broker.
- Tăng tốc độ log để đo ingestion throughput và search latency.
- Tắt collector/storage để kiểm tra mất log và recovery.

### Rủi ro

Kafka + OpenSearch tốn tài nguyên. Nếu máy yếu, dùng Fluent Bit + Loki + Grafana và dữ liệu vừa phải.

**Độ khó:** 3/5. **Tính DevOps:** 5/5. **Phù hợp:** nhóm muốn đi sâu logging thay vì làm metrics/traces toàn diện.

---

## 6. #10 - Secure Cloud Application: IAM & Zero-trust Demo

### Bài toán và giá trị

Đây là hướng **DevSecOps**: ứng dụng nhiều vai trò, least privilege, RBAC/IAM, network policy, secret management và security scan.

### Phạm vi đề xuất

- API có vai trò admin/user.
- Kubernetes RBAC phân tách quyền deploy, quan sát và quản trị.
- NetworkPolicy chỉ cho phép các service cần thiết giao tiếp.
- Secret không hard-code trong source; dùng Kubernetes Secret hoặc Vault demo.
- Chạy image/dependency scan bằng Trivy và xử lý kết quả.

### Thí nghiệm và rủi ro

- Chứng minh user thường không gọi được admin API.
- Chứng minh network traffic không được cấp phép bị chặn.
- So sánh image trước/sau xử lý lỗ hổng.
- Phải có threat model và bằng chứng mitigation; không chỉ liệt kê công cụ.

**Độ khó:** 3/5. **Tính DevSecOps:** 5/5. **Phù hợp:** nhóm thích security và Kubernetes.

---

## 7. #22 - Fault Injection & Chaos Engineering

### Bài toán

Chủ động phá hệ thống bằng pod/process crash, CPU overload, network delay, packet loss hoặc dependency failure; sau đó đo resilience và áp dụng mitigation.

### Phạm vi đề xuất

1. Dựng app microservice gồm 3 component và dashboard.
2. Tiêm 3 loại lỗi: pod crash, network delay, dependency unavailable.
3. Thêm retry/timeout/replica/circuit breaker.
4. So sánh availability, latency, error rate và recovery time trước/sau mitigation.

### Rủi ro

Kubernetes, Chaos Mesh/LitmusChaos và network fault có đường học dốc. Chỉ chọn khi ít nhất một thành viên thành thạo Docker/Kubernetes.

**Độ khó:** 4/5. **Tính DevOps/SRE:** 5/5. **Phù hợp:** nhóm mạnh; không phải lựa chọn an toàn cho nhóm mới.

---

## 8. #29 - Multi-Cloud Deployment & Failover

### Bài toán và phạm vi an toàn

Chạy cùng một ứng dụng ở hai môi trường độc lập, theo dõi health và chuyển traffic khi một môi trường hỏng. Có thể mô phỏng “multi-cloud” bằng hai Kubernetes cluster local hoặc hai Docker environment; không cần trả phí hai cloud thật.

### Phạm vi đề xuất

- Deploy cùng một stateless API ở hai environment.
- Health check, reverse proxy/load balancer và failover thủ công trước; automation là bước tiếp theo.
- Database replication là phần nâng cao; có thể dùng data mock/read-only để tránh vượt scope.
- Tắt environment A, đo downtime, failover time, latency trước/sau chuyển traffic.

### Rủi ro

Nặng về DNS, network, state/data consistency và tài nguyên máy. Đề tài dễ trở thành “chưa cấu hình xong hạ tầng”, nên chỉ chọn khi nhóm đã có nền tảng Kubernetes và scope rất chặt.

**Độ khó:** 4/5. **Tính DevOps:** 5/5. **Phù hợp:** nhóm mạnh.

---

## Đề tài DevOps bổ trợ

| Đề tài | Mức phù hợp | Nhận xét |
| --- | ---: | --- |
| #3 - Dynamic Resource Allocation | 4/5 | Hợp CloudOps; cần controller/logic cấp phát và so sánh static/dynamic |
| #24 - Service Discovery | 4/5 | Hợp microservices platform; dùng Consul/etcd, health check, node churn |
| #4 - Load Balancing | 3/5 | Hợp nền tảng; cần observability để không chỉ là cấu hình NGINX/HAProxy |
| #5 - VM vs Container vs Serverless | 3/5 | Hợp FinOps/CloudOps; mạnh ở benchmark và phân tích chi phí |
| #2 - Serverless Pipeline | 3/5 | Hợp CloudOps; làm qua cloud hoặc dựng OpenFaaS local |

## Lộ trình chọn đề tài

1. **Nhóm mới với DevOps:** chọn **#9**; dựng Docker Compose trước, Kubernetes là phần mở rộng.
2. **Muốn học Kubernetes để đi làm:** chọn **#1**; dùng Minikube/Kind local, tập trung HPA và benchmark.
3. **Mạnh backend, hạ tầng vừa phải:** chọn **#23**, kèm Prometheus/Grafana.
4. **Thích automation và vận hành:** chọn **#7**.
5. **Đã quen Kubernetes/network:** cân nhắc **#22**; chỉ chọn **#29** khi có môi trường và phân công rõ.

## Phân công gợi ý cho nhóm 4 người (đề tài #9)

| Thành viên | Trách nhiệm |
| --- | --- |
| 1 | Ứng dụng mẫu, API và kịch bản tải/lỗi |
| 2 | Docker Compose/Kubernetes, network và deployment |
| 3 | Prometheus, Grafana, Loki/trace và alerting |
| 4 | Thí nghiệm, số liệu, runbook, postmortem và báo cáo |

Mọi thành viên nên hiểu kiến trúc tổng thể, cùng chạy thí nghiệm và chuẩn bị demo.
