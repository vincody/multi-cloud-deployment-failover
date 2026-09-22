const elements = {
  healthBanner: document.querySelector(".status-banner"),
  healthStatus: document.querySelector("#health-status"),
  environment: document.querySelector("#environment"),
  region: document.querySelector("#region"),
  version: document.querySelector("#version"),
  commit: document.querySelector("#commit"),
  responseTime: document.querySelector("#response-time"),
  uptime: document.querySelector("#uptime"),
  startedAt: document.querySelector("#started-at"),
  requestCount: document.querySelector("#request-count"),
  datasetHash: document.querySelector("#dataset-hash"),
  lastUpdated: document.querySelector("#last-updated"),
  deviceList: document.querySelector("#device-list"),
  deviceSummary: document.querySelector("#device-summary"),
  refreshButton: document.querySelector("#refresh-button"),
};

const dateFormatter = new Intl.DateTimeFormat("vi-VN", {
  dateStyle: "medium",
  timeStyle: "medium",
});

function formatDuration(totalSeconds) {
  const seconds = Math.floor(totalSeconds);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function statusLabel(status) {
  return status.replaceAll("_", " ");
}

function renderDevices(devices) {
  elements.deviceList.replaceChildren(
    ...devices.map((device) => {
      const row = document.createElement("tr");
      const values = [device.id, device.name, device.type, device.location];
      values.forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      });
      const statusCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `badge badge-${device.status}`;
      badge.textContent = statusLabel(device.status);
      statusCell.append(badge);
      row.append(statusCell);
      return row;
    }),
  );
  elements.deviceSummary.textContent = `${devices.length} thiết bị mẫu`;
}

async function refreshDashboard() {
  const started = performance.now();
  elements.refreshButton.disabled = true;
  try {
    const [statusResponse, devicesResponse] = await Promise.all([
      fetch("/api/status", { cache: "no-store" }),
      fetch("/api/devices", { cache: "no-store" }),
    ]);
    if (!statusResponse.ok || !devicesResponse.ok) {
      throw new Error(`API response: ${statusResponse.status}/${devicesResponse.status}`);
    }

    const [status, devicesPayload] = await Promise.all([
      statusResponse.json(),
      devicesResponse.json(),
    ]);
    const elapsed = Math.round(performance.now() - started);
    elements.healthBanner.classList.remove("is-unavailable");
    elements.healthBanner.classList.add("is-healthy");
    elements.healthStatus.textContent = "Healthy";
    elements.environment.textContent = status.environment;
    elements.region.textContent = status.region;
    elements.version.textContent = status.version;
    elements.commit.textContent = `commit ${status.commit_sha}`;
    elements.responseTime.textContent = `${elapsed} ms`;
    elements.uptime.textContent = formatDuration(status.uptime_seconds);
    elements.startedAt.textContent = `Started ${dateFormatter.format(new Date(status.started_at))}`;
    elements.requestCount.textContent = status.request_count.toLocaleString("vi-VN");
    elements.datasetHash.textContent = status.dataset_sha256.slice(0, 12);
    elements.lastUpdated.textContent = `Cập nhật ${dateFormatter.format(new Date())}`;
    renderDevices(devicesPayload.devices);
  } catch (error) {
    elements.healthBanner.classList.remove("is-healthy");
    elements.healthBanner.classList.add("is-unavailable");
    elements.healthStatus.textContent = "Unavailable";
    elements.lastUpdated.textContent = `Không thể gọi API: ${error.message}`;
  } finally {
    elements.refreshButton.disabled = false;
  }
}

elements.refreshButton.addEventListener("click", refreshDashboard);
refreshDashboard();
window.setInterval(refreshDashboard, 3000);
