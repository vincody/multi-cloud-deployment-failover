# Phương án nâng cấp: Kubernetes và database đa cloud

Ngày cập nhật: 2026-10-04.

Tài liệu ghi nhận phương án phát triển đã trao đổi. Đây là thiết kế đề xuất, chưa phải xác nhận các thành phần đã được triển khai hoặc kiểm thử.

## 1. Mục tiêu

- Mở rộng project Multi-Cloud Failover AWS–Azure để phục hồi cả ứng dụng và dữ liệu.
- Dùng Kubernetes để quản lý triển khai và tự phục hồi ứng dụng trong từng cloud.
- Duy trì khả năng đọc và ghi dữ liệu sau khi chuyển dịch vụ sang Azure.
- Đánh giá thời gian gián đoạn, độ trễ đồng bộ và dữ liệu chưa kịp sao chép khi xảy ra sự cố.

## 2. Tài nguyên và kiến trúc đề xuất

Người dùng dự kiến nâng mỗi VM từ 2 GB lên 4 GB RAM. Mỗi VM nên có ít nhất 2 vCPU. K3s server yêu cầu tối thiểu 2 core CPU và 2 GB RAM, chưa tính workload; 4 GB là cấu hình đề xuất cho demo nhỏ, cần đo tài nguyên và thử tải trước khi kết luận đủ.

| Thành phần | AWS EC2 | Azure VM |
|---|---|---|
| Vai trò ban đầu | Môi trường chính | Môi trường dự phòng chạy sẵn |
| Kubernetes | Một cluster K3s độc lập, một node | Một cluster K3s độc lập, một node |
| Ứng dụng | FastAPI, ban đầu một Pod | Cùng phiên bản FastAPI, ban đầu một Pod |
| Database | PostgreSQL primary, đọc/ghi | PostgreSQL standby, nhận replication |
| Dữ liệu lưu trữ | Volume bền vững tại AWS | Volume bền vững tại Azure |

- Prometheus, Grafana, Blackbox Exporter và k6 chạy trên laptop hoặc máy thứ ba để giảm tải cho hai VM.
- Giữ Route 53 làm cơ chế DNS failover giữa hai endpoint ứng dụng.
- Không ghép hai VM khác cloud vào cùng một cluster trong phạm vi phương án này.
- PostgreSQL có thể chạy bằng StatefulSet và PVC trong K3s; cách triển khai cụ thể cần chốt trước khi thực hiện. PVC cục bộ không tự sao chép dữ liệu sang cloud khác và không thay thế backup.
- Hai cluster một node không có khả năng chịu lỗi node bên trong từng cluster. Khi VM chết, cơ chế failover sang cloud còn lại đảm nhiệm phục hồi dịch vụ.

## 3. Vai trò của Kubernetes và Docker image

- GitHub Actions build image và phát hành lên GHCR như workflow hiện có.
- Deployment trên hai cluster khai báo cùng image digest để chạy cùng phiên bản code. Registry riêng tư cần thông tin xác thực để kéo image.
- Kubernetes dùng container runtime để kéo image và chạy Pod; không cần chạy Docker Compose song song cho cùng ứng dụng.
- Liveness probe phát hiện container bị treo; Deployment tạo Pod thay thế khi Pod bị mất; readiness probe loại Pod chưa sẵn sàng khỏi luồng phục vụ.
- Kubernetes không tự đồng bộ database hoặc tự thực hiện failover PostgreSQL giữa hai cluster chỉ bằng Deployment/StatefulSet.
- Dữ liệu phát sinh lưu trong database và volume riêng, không nằm trong image ứng dụng. Hai bên kéo cùng image PostgreSQL không có nghĩa dữ liệu giống nhau.

## 4. Nghiệp vụ và database

**Đã chốt nghiệp vụ mới: Issues mini, hệ thống ticket hỗ trợ theo kiểu GitHub Issues.** Thay thế lựa chọn quản lý công việc trước đó. Đã có hai web local AWS/Azure, PostgreSQL primary/standby với replication, chế độ chỉ đọc, proxy dùng chung cổng 8090 và quy trình failover/rejoin/failback có kiểm soát. Khi dựng lại standby, volume cũ được giữ nguyên. Kubernetes, triển khai cloud, giám sát tự động và DNS cloud là các bước tiếp theo. Hướng dẫn: [LOCAL_REPLICATION.md](../LOCAL_REPLICATION.md).

Schema nghiệp vụ gồm bốn bảng:

| Bảng | Nội dung |
|---|---|
| `issues` | UUID, số issue, tiêu đề, mô tả, tên người gửi, Open/Closed, version, thời gian tạo/cập nhật |
| `comments` | UUID, issue liên quan, tên người bình luận, nội dung, thời gian tạo |
| `issue_history` | Issue liên quan, mã yêu cầu, loại hoạt động tạo/sửa/đóng/mở lại/bình luận, dữ liệu trước/sau và thời điểm |
| `save_requests` | Mã yêu cầu lưu duy nhất, nội dung và loại thao tác, kết quả để kiểm tra khi retry |

Chức năng tối thiểu: tạo issue, xem danh sách/chi tiết, sửa tiêu đề/mô tả, thêm bình luận, đóng/mở lại và xem lịch sử hoạt động. Mục đích là chứng minh issue và bình luận đã ghi trên AWS xuất hiện trên Azure, tiếp tục xử lý được sau failover.

- Trạng thái: Open và Closed. Issue mới mặc định Open; bình luận được phép cả khi issue đã Closed.
- Giao diện gồm danh sách, form tạo/sửa và chi tiết issue với luồng bình luận; giữ nhãn API đang phục vụ AWS/Azure/Local. Grafana riêng giám sát hạ tầng.
- Người gửi nhập tên trực tiếp, chưa có đăng nhập, phân quyền, repository, pull request hoặc thông báo.
- Lưu issue hoặc bình luận, lịch sử và kết quả yêu cầu trong cùng transaction; mã yêu cầu có ràng buộc duy nhất để hỗ trợ retry. Không chấp nhận cùng mã yêu cầu cho nội dung khác.
- Dùng version bản ghi để phát hiện xung đột chỉnh sửa. Bản nháp chưa Save nằm trên trình duyệt theo phương án ở mục 5.

### Kịch bản demo nghiệp vụ đã chốt

1. Tạo issue “Không thể đăng nhập” trên AWS, xác minh xuất hiện ở standby Azure.
2. Thêm bình luận, xác minh đồng bộ rồi dừng AWS; sau failover, ứng dụng Azure vẫn hiển thị issue cùng bình luận.
3. Trên Azure, thêm câu trả lời và đóng issue, xác minh dữ liệu và lịch sử hoạt động được lưu. Tái đồng bộ AWS trước khi chuyển về.
4. Đang viết mô tả hoặc bình luận chưa gửi, gây sự cố rồi tải lại qua domain chung; khôi phục bản nháp và gửi trên Azure.
5. Dùng script tạo/cập nhật issue và gửi bình luận liên tục với mã yêu cầu riêng, dừng AWS bất ngờ rồi đối chiếu dữ liệu Azure để phát hiện thao tác bị mất hoặc ghi trùng.

Kịch bản 2 chờ đồng bộ để minh họa dữ liệu được giữ lại; kịch bản 5 gây sự cố bất ngờ để đo giới hạn của replication bất đồng bộ, không giả định mọi giao dịch đều đã sang Azure.

## 5. Đồng bộ dữ liệu giữa hai database

- Dùng hai PostgreSQL riêng, cùng major version tương thích cho physical streaming replication.
- Khởi tạo standby Azure từ bản sao nền của PostgreSQL AWS, sau đó liên tục nhận và áp dụng WAL (nhật ký thay đổi dữ liệu).
- Ban đầu chỉ PostgreSQL AWS nhận ghi; Azure là standby, có thể đọc nếu cấu hình hot standby.
- Đề xuất replication bất đồng bộ cho bản demo. Các giao dịch cuối cùng có thể chưa sang Azure khi AWS gặp sự cố; không cam kết mất dữ liệu bằng không.
- Cần kết nối replication được bảo vệ giữa hai cloud, xác thực riêng và giới hạn nguồn truy cập; không mở database công khai cho mọi IP.
- Theo dõi độ trễ replication và lượng WAL giữ lại để tránh đầy đĩa khi đường truyền gián đoạn.
- Database ứng dụng này khác với datastore nội bộ lưu trạng thái Kubernetes.

```text
Bình thường:
Người dùng → App AWS → PostgreSQL AWS (primary, đọc/ghi)
                           │
                           └── WAL replication → PostgreSQL Azure (standby)

Sau failover:
Người dùng → App Azure → PostgreSQL Azure (primary, đọc/ghi)
```

### Dữ liệu đang chỉnh sửa khi xảy ra sự cố

Replication chỉ sao chép dữ liệu đã ghi vào database; nội dung người dùng đang nhập trên trình duyệt nhưng chưa Save không tự được đồng bộ sang Azure.

| Thời điểm AWS gặp sự cố | Trạng thái và cách xử lý |
|---|---|
| Chưa bấm Save | Nội dung thường chỉ ở trình duyệt, chưa vào database. Trang còn mở có thể giữ nội dung; tải lại trang có thể làm mất nếu chưa lưu bản nháp. |
| Đã bấm Save nhưng chưa nhận phản hồi | Kết quả chưa xác định: giao dịch có thể đã commit hoặc chưa. Kiểm tra theo mã yêu cầu trước khi gửi lại, tránh ghi trùng. |
| Đã nhận thông báo lưu thành công | Dữ liệu đã commit tại AWS, nhưng với replication bất đồng bộ vẫn có thể chưa tới Azure trước sự cố. |

Phương án đề xuất để bảo vệ nội dung chưa lưu:

- Lưu bản nháp định kỳ trên trình duyệt bằng `localStorage`, gắn với người dùng và ID bản ghi hoặc ID bản nháp mới.
- Hai môi trường dùng cùng origin của web (scheme, hostname và port) để trình duyệt có thể đọc lại bản nháp sau failover; domain riêng AWS/Azure không dùng chung `localStorage`.
- Khi tải lại trang, thông báo có bản nháp và cho người dùng xem lại/khôi phục trước khi Save vào môi trường đang phục vụ.
- Giữ bản nháp khi lưu lỗi hoặc kết quả chưa xác định. Chỉ đánh dấu/xóa bản nháp tương ứng sau khi xác minh lưu thành công; nếu người dùng tiếp tục chỉnh sửa trong lúc chờ phản hồi, không xóa phần nội dung mới.
- Bản nháp chỉ được giữ trên trình duyệt/origin đó, không tự chuyển sang thiết bị khác và có thể mất khi người dùng xóa dữ liệu trình duyệt. Không dùng để lưu secret hoặc dữ liệu nhạy cảm nếu chưa có thiết kế bảo vệ phù hợp.

Phương án đề xuất để xử lý yêu cầu Save và retry:

- Gắn mã yêu cầu duy nhất cho mỗi thao tác lưu; phía server dùng cơ chế idempotency để nhận diện yêu cầu gửi lại và tránh áp dụng cùng thao tác nhiều lần.
- Với chỉnh sửa, dùng version của bản ghi để phát hiện dữ liệu đã thay đổi, cho người dùng đối chiếu trước khi ghi đè bản nháp.
- Mã yêu cầu/lịch sử chống ghi trùng cũng cần được lưu trong database và replication. Nếu bản ghi xác nhận chỉ có ở AWS, Azure có thể chưa biết yêu cầu đã xử lý; idempotency không tự bảo đảm ghi đúng một lần xuyên failover khi replication bất đồng bộ mất dữ liệu.
- Autosave lên server là lựa chọn bổ sung, nhưng vẫn chịu rủi ro yêu cầu đang xử lý hoặc chưa kịp replication. Không thay thế lưu bản nháp trên trình duyệt.

Kịch bản kiểm thử bổ sung:

1. Nhập nội dung nhưng chưa Save, dừng AWS, tải lại qua domain chung sau failover và xác minh bản nháp được khôi phục để người dùng lưu trên Azure.
2. Gây mất phản hồi trong lúc Save, kiểm tra kết quả theo mã yêu cầu và gửi lại để đánh giá hành vi chống ghi trùng.
3. Lưu thành công ngay trước sự cố, đối chiếu dữ liệu trên Azure để đo phần chưa kịp đồng bộ.

## 6. Quy trình failover và failback

### Failover AWS → Azure

1. Phát hiện và xác minh sự cố; không coi riêng việc mất kết nối giữa hai cloud là bằng chứng AWS đã dừng.
2. Ngăn primary cũ tiếp tục nhận ghi (fencing) trước khi promote Azure, tránh hai database cùng nhận ghi và dữ liệu phân kỳ. Nếu chưa thể bảo đảm fencing, không tự động promote.
3. Kiểm tra dữ liệu/WAL hiện có và ghi nhận độ trễ replication cuối cùng quan sát được.
4. Promote PostgreSQL Azure thành primary bằng cơ chế PostgreSQL.
5. Kiểm tra App Azure kết nối database Azure và thực hiện đọc/ghi thành công.
6. Chỉ sau đó mới cho endpoint Azure đạt điều kiện sẵn sàng cho DNS failover; cấu hình health check phải phản ánh khả năng phục vụ đầy đủ, không chỉ tiến trình còn chạy.
7. Route 53 chuyển DNS sang Azure. DNS cache, TTL và kết nối cũ có thể làm một số client chuyển chậm hơn.

Trong giai đoạn chuyển đổi, thao tác thêm/sửa có thể tạm gián đoạn. Sau khi Azure sẵn sàng, người dùng tiếp tục đọc/ghi tại Azure. Route 53 chỉ chuyển DNS, không promote database; cần quy trình điều phối riêng.

### AWS phục hồi và failback

- Không tự động đưa lưu lượng về AWS chỉ vì VM đã chạy lại.
- Giữ PostgreSQL AWS cũ không nhận ghi; đưa AWS trở thành standby của Azure bằng quy trình tái đồng bộ phù hợp, có thể cần dựng lại từ bản sao mới.
- Xác minh AWS đã bắt kịp dữ liệu phát sinh trên Azure.
- Nếu cần chuyển lại, thực hiện switchover có kiểm soát: tạm ngừng ghi, bảo đảm đồng bộ, đổi vai trò database và xác minh ứng dụng trước khi chuyển DNS.
- Điều chỉnh cơ chế failback trong thiết kế hiện tại để không trả người dùng về ứng dụng gắn với database cũ hoặc chưa sẵn sàng.

Giai đoạn đầu đề xuất chạy script có kiểm soát để xác minh quy trình. Chỉ tự động hóa sau khi có cơ chế fencing và tiêu chí chuyển đổi rõ ràng.

## 7. Phạm vi triển khai và kiểm thử

1. Hoàn thiện Issues mini với bốn bảng nghiệp vụ, API đọc/ghi, bình luận và xử lý bản nháp/retry; kiểm thử local trước.
2. Nâng VM, kiểm tra CPU/RAM/đĩa, triển khai K3s và ứng dụng không database trước để đo mức sử dụng nền.
3. Bổ sung PostgreSQL, volume bền vững và replication AWS → Azure.
4. Hoàn thiện readiness, fencing, script promote và điều kiện DNS failover.
5. Chạy kịch bản demo và lưu số đo, log, ảnh chụp vào `experiments/`.

Các kịch bản cần đánh giá:

- Xóa Pod ứng dụng để quan sát Kubernetes tạo Pod thay thế.
- Tạo/sửa dữ liệu trên AWS và xác minh standby Azure đã nhận thay đổi.
- Dừng VM AWS, chuyển sang Azure và kiểm tra dữ liệu cũ cùng thao tác ghi mới.
- Ngắt replication để đo độ trễ, phát hiện sự cố và tránh promote sai khi AWS vẫn đang nhận ghi.
- Khôi phục AWS, tái đồng bộ từ Azure và kiểm thử switchover có kiểm soát.

Ghi nhận thời gian khôi phục thực tế để đối chiếu RTO mục tiêu, mức dữ liệu mất thực tế để đối chiếu RPO mục tiêu, tỷ lệ lỗi và độ trễ request. Dùng ID giao dịch để phát hiện thiếu hoặc ghi trùng khi client thử lại.

Với 4 GB RAM, bắt đầu một Pod ứng dụng, ít kết nối PostgreSQL và tải nhỏ; đặt requests/limits sau khi đo tài nguyên. Chưa đưa Argo CD, service mesh hoặc bộ monitoring đầy đủ vào hai cluster. Cần backup riêng vì replication cũng có thể sao chép thao tác xóa nhầm.

## 8. Monitoring độc lập ngoài hai VM

Đặt dashboard giám sát trên laptop hoặc máy thứ ba, độc lập với web nghiệp vụ và hai VM AWS/Azure. Khi web hoặc một VM gặp sự cố, người vận hành vẫn truy cập Grafana và xem lịch sử đã thu thập. Máy monitoring phải hoạt động và có kết nối mạng trong suốt demo.

### Thành phần và kết nối

- Mỗi cluster K3s chạy `kube-state-metrics` để xuất trạng thái các đối tượng Kubernetes, gồm Pod và Deployment.
- Prometheus chạy ngoài hai VM, thu thập metrics từ cả hai cluster và lưu dữ liệu lịch sử.
- Grafana chạy cùng môi trường monitoring, dùng Prometheus làm nguồn dữ liệu và hiển thị cả AWS/Azure trên một dashboard.
- Blackbox Exporter kiểm tra endpoint AWS, endpoint Azure và domain chung từ bên ngoài để đánh giá khả năng truy cập thực tế.
- Kết nối tới endpoint metrics qua VPN hoặc SSH tunnel; dùng quyền chỉ đọc phù hợp và không mở API Kubernetes/metrics cho toàn Internet.
- Gắn nhãn nguồn như `cluster="aws"` và `cluster="azure"` để phân biệt dữ liệu.

```text
VM AWS:   K3s + các Pod + kube-state-metrics ──┐
                                             ├→ Prometheus → Grafana
VM Azure: K3s + các Pod + kube-state-metrics ──┘   (laptop/máy thứ ba)

Blackbox Exporter → kiểm tra AWS, Azure và domain chung
                 → Prometheus lưu kết quả → Grafana hiển thị
```

### Thông tin cần hiển thị

| Nhóm | Nội dung |
|---|---|
| Pod | Cluster, namespace, tên Pod, phase, điều kiện Ready |
| Container | Số lần restart và trạng thái chờ như CrashLoopBackOff khi có |
| Deployment | Số replica mong muốn, số Ready/Available, tiến trình scale |
| Nguồn giám sát | Endpoint metrics có thu thập được không, thời điểm dữ liệu gần nhất |
| Truy cập ứng dụng | Kết quả probe và độ trễ của AWS, Azure, domain chung |

Nếu cần CPU/RAM từng container, bổ sung nguồn metrics của kubelet/container; `kube-state-metrics` không cung cấp mức sử dụng tài nguyên thực tế. Phân biệt Pod Running với Pod Ready, vì tiến trình chạy chưa có nghĩa ứng dụng sẵn sàng phục vụ.

### Tự cập nhật và hành vi khi có sự cố

- Dashboard tự cập nhật khi Pod lỗi, container restart, Pod thay thế xuất hiện hoặc Deployment tăng/giảm replica.
- Truy vấn theo cluster, namespace và nhãn ứng dụng/Deployment thay vì cố định tên từng Pod để Pod mới tự được đưa vào thống kê.
- Cấu hình khởi đầu đề xuất: Prometheus scrape mỗi 15 giây, Grafana refresh mỗi 5 giây. Đây là cập nhật định kỳ, có độ trễ; sự kiện quá nhanh giữa hai lần scrape có thể không hiện đủ trạng thái trung gian.
- Khi VM AWS dừng, nguồn metrics AWS mất kết nối và probe ứng dụng AWS thất bại; dashboard bên ngoài và dữ liệu Azure vẫn cập nhật.
- Không coi metrics AWS cuối cùng là trạng thái hiện tại. Dashboard phải biểu thị mất kết nối/không có dữ liệu mới; không suy luận mọi Pod đã Failed chỉ từ việc không scrape được.
- Lịch sử trước sự cố được giữ tại Prometheus bên ngoài; log/sự kiện Kubernetes cần giải pháp thu thập riêng nếu muốn lưu đầy đủ.
- Monitoring cung cấp quan sát và cảnh báo; không tự promote database chỉ vì mất metrics. Quy trình failover vẫn tuân thủ xác minh và fencing ở mục 6.

### Kịch bản demo bổ sung

1. Mở dashboard Grafana chung cho AWS và Azure trước khi gây sự cố.
2. Xóa một Pod ứng dụng: quan sát số replica sẵn sàng và Pod thay thế xuất hiện.
3. Scale Deployment ứng dụng từ 1 lên 3 replica nếu tài nguyên cho phép: quan sát replica mong muốn tăng trước, số Ready tăng sau.
4. Dừng VM AWS: quan sát mất kết nối metrics và lỗi endpoint AWS, đồng thời theo dõi Azure trong quá trình failover.
5. Lưu timeline, tỷ lệ lỗi và độ trễ để đối chiếu với các bước chuyển đổi database/DNS.

## 9. Tên đề tài gợi ý và các quyết định còn mở

Tên đề xuất: **Triển khai và đánh giá khả năng phục hồi ứng dụng đa đám mây AWS–Azure bằng Kubernetes, đồng bộ PostgreSQL và DNS Failover.**

Các quyết định cần chốt trước khi triển khai:

- Migration/schema production của bốn bảng Issues mini; schema local đã có trong `database/schema/issues.sql`, nghiệp vụ đã chốt ở mục 4.
- Cách chạy PostgreSQL, quản lý volume và backup.
- Kết nối bảo vệ giữa hai cloud và cơ chế fencing.
- Mục tiêu RTO/RPO, tải thử và mức tự động hóa failover.

## 10. Tài liệu tham khảo

- [K3s: yêu cầu tài nguyên](https://docs.k3s.io/installation/requirements).
- [Kubernetes: tự phục hồi](https://kubernetes.io/docs/concepts/architecture/self-healing/).
- [Kubernetes: liveness, readiness và startup probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/).
- [PostgreSQL: standby và streaming replication](https://www.postgresql.org/docs/current/warm-standby.html).
- [PostgreSQL: failover và promote](https://www.postgresql.org/docs/16/warm-standby-failover.html).
- [kube-state-metrics: trạng thái Kubernetes cho Prometheus](https://github.com/kubernetes/kube-state-metrics).
- [Prometheus: kiểm tra endpoint bằng Blackbox Exporter](https://prometheus.io/docs/guides/multi-target-exporter/).
