const elements = {
  healthStatus: document.querySelector("#health-status"),
  healthText: document.querySelector("#health-text"),
  cloudLabel: document.querySelector("#cloud-label"),
  environment: document.querySelector("#environment"),
  region: document.querySelector("#region"),
  uptime: document.querySelector("#uptime"),
  responseTime: document.querySelector("#response-time"),
  performanceState: document.querySelector("#performance-state"),
  requestsMinute: document.querySelector("#requests-minute"),
  errorRate: document.querySelector("#error-rate"),
  p95Latency: document.querySelector("#p95-latency"),
  lastChecked: document.querySelector("#last-checked"),
  refreshButton: document.querySelector("#refresh-button"),
};

const originNames = {
  "aws-primary": "AWS Primary",
  "azure-standby": "Azure Standby",
  local: "Local Preview",
};

let isRefreshing = false;
function clearPerformance(label) {
  elements.performanceState.textContent = label;
  elements.requestsMinute.textContent = "—";
  elements.errorRate.textContent = "—";
  elements.p95Latency.textContent = "—";
}

function renderPerformance(summary) {
  if (!summary || !Number.isFinite(summary.requests)) {
    clearPerformance("Chưa có dữ liệu");
    return;
  }
  elements.performanceState.textContent = summary.requests > 0 ? "Đang cập nhật" : "Chưa có lưu lượng";
  elements.requestsMinute.textContent = summary.requests.toLocaleString("vi-VN");
  elements.errorRate.textContent = summary.error_percent === null ? "—" : summary.error_percent + "%";
  elements.p95Latency.textContent = summary.p95_ms === null ? "—" : summary.p95_ms + " ms";
}

function formatUptime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  const clock = [hours, minutes, remainingSeconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
  return days > 0 ? days + " ngày " + clock : clock;
}

function updateHealth(state, label) {
  elements.healthStatus.classList.remove("is-checking", "is-healthy", "is-unavailable");
  elements.healthStatus.classList.add("is-" + state);
  elements.healthText.textContent = label;
}

async function refreshStatus() {
  if (isRefreshing || document.hidden) return;

  isRefreshing = true;
  elements.refreshButton.disabled = true;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  const started = performance.now();

  try {
    const response = await fetch("/api/status?ts=" + Date.now(), {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("HTTP " + response.status);

    const status = await response.json();
    if (status.status !== "healthy") throw new Error("Service not ready");

    updateHealth("healthy", "Đang hoạt động");
    elements.cloudLabel.textContent =
      originNames[status.environment] || status.environment || "Không xác định";
    elements.environment.textContent = status.environment || "—";
    elements.region.textContent = status.region || "—";
    elements.uptime.textContent = formatUptime(status.uptime_seconds);
    elements.responseTime.textContent = Math.round(performance.now() - started) + " ms";
    renderPerformance(status.workload_60s);
  } catch {
    updateHealth("unavailable", "Không kết nối");
    elements.cloudLabel.textContent = "Không khả dụng";
    elements.environment.textContent = "—";
    elements.region.textContent = "—";
    elements.uptime.textContent = "—";
    elements.responseTime.textContent = "—";
    clearPerformance("Không thể kiểm tra");
  } finally {
    window.clearTimeout(timeout);
    elements.lastChecked.textContent =
      "Kiểm tra lúc " + new Intl.DateTimeFormat("vi-VN", { timeStyle: "medium" }).format(new Date());
    elements.refreshButton.disabled = false;
    isRefreshing = false;
  }
}

elements.refreshButton.addEventListener("click", refreshStatus);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshStatus();
});
refreshStatus();
window.setInterval(refreshStatus, 5000);
