# Giai đoạn 4 — Deploy Azure Container Apps standby

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** tạo Azure standby độc lập, chạy đúng image digest của AWS nhưng có `APP_ENV=azure-standby`.

## 1. Đầu vào

- Azure subscription, quota và quyền tạo Resource Group/Container Apps.
- Azure CLI đã login và chọn đúng subscription.
- GHCR image digest từ GĐ2.
- GHCR `read:packages` credential nếu package private.
- Kết quả `/version` AWS để so sánh.

Dự kiến dùng Consumption profile, `minReplicas=1` và `maxReplicas=1` trong bài đo chuẩn. Cấu hình này giảm cold start nhưng không mặc định miễn phí.

## 2. Tài nguyên sẽ tạo

| Resource | Tên đề xuất | Vai trò |
|---|---|---|
| Resource Group | `dcs29-rg` | Nhóm tài nguyên để kiểm kê/dọn |
| Container Apps Environment | `dcs29-env` | Môi trường quản lý |
| Container App | `dcs29-standby` | Standby public HTTPS |
| Revision | tự sinh | Gắn image/config |
| Log workspace | theo lựa chọn | Log/retention, cần theo dõi phí |

Region minh họa `southeastasia`; phải kiểm tra availability/quota thật trước khi tạo.

## 3. Chuẩn bị Azure CLI

```bash
az login
az account show
az account set --subscription YOUR_SUBSCRIPTION_ID
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

Đợi provider registration hoàn tất. Không đổi sang Dedicated chỉ để né quota mà không ghi chi phí.

## 4. Tạo environment

```bash
az group create --name dcs29-rg --location southeastasia --tags Project=dcs29
az containerapp env create --name dcs29-env --resource-group dcs29-rg --location southeastasia
```

Lưu subscription ID đã che bớt, resource IDs, region, thời gian tạo và cấu hình logging/retention.

## 5. Cấu hình registry

Nếu package private, tạo secret registry bằng Portal/CLI, không ghi password vào Git. Credential chỉ cần pull package. Kiểm tra package path viết thường và digest đầy đủ.

Nếu package public, không cần registry secret nhưng vẫn phải deploy theo digest. Visibility public/private là quyết định nhóm và phải ghi vào báo cáo.

## 6. Tạo Container App

Lệnh là mẫu và cần đối chiếu CLI hiện hành lúc chạy:

```bash
az containerapp create --name dcs29-standby --resource-group dcs29-rg --environment dcs29-env --image ghcr.io/vincody/multi-cloud-deployment-failover@sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5 --workload-profile-name Consumption --ingress external --target-port 8080 --cpu 0.25 --memory 0.5Gi --min-replicas 1 --max-replicas 1 --env-vars APP_ENV=azure-standby APP_REGION=southeastasia
```

Nếu private registry, bổ sung registry server/username/secret theo CLI hoặc Portal. Không đặt token trực tiếp trong command được chụp màn hình/history.

## 7. Health probes và ingress

- External ingress bật HTTPS.
- Target port 8080.
- Liveness path `/health/live`.
- Readiness path `/health/ready`.
- Cho startup đủ thời gian pull/start.
- Không expose `/metrics` công khai nếu chưa có auth/allowlist.

Lấy FQDN:

```bash
az containerapp show --name dcs29-standby --resource-group dcs29-rg --query properties.configuration.ingress.fqdn --output tsv
```

## 8. Kiểm tra Azure độc lập

```bash
curl -f https://AZURE_FQDN/health/ready
curl -f https://AZURE_FQDN/version
curl -f https://AZURE_FQDN/api/devices
```

So với AWS:

| Trường | Phải giống | Phải khác |
|---|---|---|
| version, commit, dataset SHA | Có | Không |
| environment | Không | `azure-standby` |
| region | Không | Azure region |

Tạm dừng app/EC2 AWS và gọi lại URL Azure trực tiếp. Azure phải vẫn phục vụ; nếu không, standby còn phụ thuộc AWS.

## 9. Kiểm tra revision/replica

1. Xác nhận revision active dùng đúng digest.
2. Xác nhận một replica ready.
3. Restart revision/replica và quan sát readiness.
4. Kiểm tra log startup, probe và request.
5. Đo cold-start riêng nếu sau này thử `minReplicas=0`; không trộn với baseline warm standby.

## 10. Rollback và dọn tài nguyên

Rollback bằng revision trước/digest trước, không rebuild trên Azure. Khi ngừng test, cân nhắc scale về 0 nhưng nhớ health probe/traffic có thể đánh thức app. Chỉ xóa Resource Group sau khi export evidence và chắc chắn không chứa tài nguyên dùng chung.

## 11. Artifact phải lưu

- Subscription/region/resource IDs đã che thông tin nhạy cảm.
- FQDN mặc định, revision, replica config.
- Image digest và environment variables không secret.
- Response health/version/devices.
- Log startup/probe.
- CPU/RAM/min-max replica và bảng chi phí.
- Bằng chứng Azure chạy khi AWS tắt.

## 12. Checklist hoàn thành

- [ ] Resource Group/Environment/App được tạo đúng region.
- [ ] Private GHCR pull hoạt động bằng secret phù hợp.
- [ ] Ingress HTTPS tới port 8080.
- [ ] Probes pass và replica ready.
- [ ] Azure dùng cùng digest AWS.
- [ ] `/version` chỉ khác environment/region.
- [ ] Azure hoạt động khi AWS dừng.
- [ ] Evidence/cost proxy đã lưu.

## 13. Kết quả mong đợi và bước tiếp theo

Sau GĐ4 có hai origin độc lập nhưng người dùng vẫn truy cập hai URL riêng. GĐ5 gắn cùng hostname và HTTPS cho cả hai. Xem [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md).
