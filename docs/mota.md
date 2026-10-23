Đồ án xây dựng hệ thống quản lý Issues với cơ chế dự phòng ứng dụng và dữ liệu giữa AWS và Azure, giúp giảm gián đoạn dịch vụ khi môi trường chính gặp sự cố.

 • AWS EC2 và Azure VM chạy hai cụm K3s độc lập, dùng cùng image ứng dụng. PostgreSQL trên AWS là primary, trên Azure là standby, đồng bộ bất đồng bộ qua WireGuard.

 • Khi AWS gặp sự cố, cần cô lập primary cũ và promote PostgreSQL Azure trước khi phục vụ ghi. Route 53 kiểm tra khả năng ghi và chuyển DNS sang Azure; người dùng giữ tên miền chung. Nhánh failover tự động (watcher chạy trên VM Azure, chỉ failover khi cả probe riêng lẫn Route 53 cùng báo AWS sập) đã có, mặc định tắt.

 • Khi AWS phục hồi, phải tái đồng bộ dữ liệu từ Azure rồi thực hiện failback theo runbook; hệ thống không tự chuyển về chỉ vì máy chủ AWS hoạt động trở lại.

 • Giao diện Issues hỗ trợ tạo/sửa, đóng/mở lại, bình luận, lịch sử và hiển thị môi trường cùng vai trò database, có Prometheus, Blackbox Exporter và dashboard Grafana cài sẵn cho Issues (probe `/health/live` và `/health/database`).

 • Đánh giá gồm probe đọc/ghi liên tục, gây sự cố, đo thời gian chuyển đổi, tỷ lệ lỗi và kiểm tra dữ liệu sau failover/failback. Local đã được kiểm chứng; triển khai cloud bằng Terraform còn cần nghiệm thu trên VM thật. Replication bất đồng bộ có thể mất dữ liệu cuối khi primary sập.
