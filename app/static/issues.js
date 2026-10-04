const $ = id => document.getElementById(id);
const issueKey = 'dcs29:issue-draft:v1';
const commentKey = id => `dcs29:comment-draft:v1:${id}`;
let issues = [], filter = 'all', editing = null, current = null;
let issuePending = null, commentPending = null, issueBusy = false, commentBusy = false;
let canWrite = false;
const date = value => new Date(value).toLocaleString('vi-VN');

function node(tag, text, className = '') {
  const result = document.createElement(tag);
  result.textContent = text;
  result.className = className;
  return result;
}
function icon(kind) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  const paths = {
    open: 'M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0Zm0 6.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z',
    closed: 'M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0Zm3.28 5.22a.75.75 0 0 1 0 1.06l-4 4a.75.75 0 0 1-1.06 0l-1.5-1.5a.75.75 0 1 1 1.06-1.06l.97.97 3.47-3.47a.75.75 0 0 1 1.06 0Z',
    comment: 'M2 1.5h12A1.5 1.5 0 0 1 15.5 3v8a1.5 1.5 0 0 1-1.5 1.5H6.31l-3.78 2.37A1 1 0 0 1 1 14v-1.59A1.5 1.5 0 0 1 .5 11V3A1.5 1.5 0 0 1 2 1.5ZM2 3v8h.5v2.1l3.38-2.1H14V3Z'
  };
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', paths[kind]); path.setAttribute('fill', 'currentColor'); svg.append(path);
  return svg;
}
function avatar(author) {
  const result = node('span', author.trim().slice(0,2).toUpperCase(), 'avatar');
  result.title = author; result.setAttribute('aria-label', author); return result;
}
function read(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
}
function store(key, value, hint) {
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
    if (hint) $(hint).textContent = value ? 'Bản nháp được giữ trên trình duyệt này.' : '';
  } catch {
    if (hint) $(hint).textContent = 'Không lưu được bản nháp. Giữ trang mở để tránh mất nội dung.';
  }
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(10000)});
  let data;
  try { data = await response.json(); } catch { throw Error('Không đọc được phản hồi. Hãy thử lại.'); }
  if (!response.ok) {
    const error = Error(typeof data.detail === 'string' ? data.detail : 'Vui lòng kiểm tra nội dung nhập.');
    error.definitive = response.status >= 400 && response.status < 500;
    throw error;
  }
  return data;
}
function issueSnapshot() {
  return {id: editing?.id || null, version: editing?.version || 0,
    author: $('issue-author').value, title: $('title').value,
    description: $('description').value, status: $('status').value};
}
function saveIssueDraft() {
  const value = issueSnapshot();
  const unchanged = editing && ['author','title','description','status'].every(field => value[field] === editing[field]);
  const empty = !editing && !value.title && !value.description;
  store(issueKey, !issuePending && (unchanged || empty) ? null : {...value, pending: issuePending}, 'draft-info');
}
function commentSnapshot() {
  return {author: $('comment-author').value, body: $('comment-body').value};
}
function saveCommentDraft() {
  if (!current) return;
  const value = commentSnapshot();
  store(commentKey(current.id), value.body || commentPending ? {...value, pending: commentPending} : null, 'comment-draft-info');
}
function render() {
  const query = $('search').value.toLocaleLowerCase('vi');
  const visible = issues.filter(issue => (filter === 'all' || issue.status === filter) &&
    (!$('author-filter').value || issue.author === $('author-filter').value) &&
    `${issue.title} ${issue.description} #${issue.number}`.toLocaleLowerCase('vi').includes(query));
  const order = $('sort').value;
  visible.sort((a,b) => order === 'oldest' ? a.number-b.number : order === 'updated' ? new Date(b.updated_at)-new Date(a.updated_at) || b.number-a.number : b.number-a.number);
  $('total').textContent = issues.length;
  $('open-count').textContent = issues.filter(issue => issue.status === 'open').length;
  $('closed-count').textContent = issues.filter(issue => issue.status === 'closed').length;
  $('list').replaceChildren();
  if (!visible.length) {
    const box = node('div', '', 'empty');
    box.append(node('h2', issues.length ? 'Không tìm thấy issue' : 'Bắt đầu một cuộc trao đổi.'),
      node('p', issues.length ? 'Thử từ khóa hoặc trạng thái khác.' : 'Tạo issue đầu tiên để ghi nhận vấn đề.'));
    $('list').append(box);
  }
  for (const issue of visible) {
    const row = node('button', '', 'task');
    row.type = 'button';
    const statusIcon = node('span', '', `issue-icon ${issue.status}`);
    statusIcon.append(icon(issue.status)); statusIcon.title = issue.status === 'open' ? 'Open' : 'Closed';
    const content = node('span', '', 'task-content');
    content.append(node('span', issue.title, 'task-title'),
      node('span', `#${issue.number} · ${issue.author} opened ${date(issue.created_at)}`, 'issue-subtitle'));
    const count = node('span', '', 'comment-count');
    count.append(icon('comment'), node('span', 'Bình luận'), node('b', String(issue.comment_count)));
    count.setAttribute('aria-label', `${issue.comment_count} bình luận`);
    row.append(statusIcon, content, count);
    row.onclick = () => openDetail(issue.id);
    $('list').append(row);
  }
}
async function load() {
  try {
    issues = await api('/api/issues');
    const selected = $('author-filter').value;
    $('author-filter').replaceChildren(new Option('Author: Tất cả', ''));
    [...new Set(issues.map(issue => issue.author))].sort().forEach(author => $('author-filter').append(new Option(author, author)));
    $('author-filter').value = selected;
    render(); $('notice').textContent = ''; return true;
  }
  catch (error) { $('notice').textContent = `${error.message} Dữ liệu đang hiển thị có thể đã cũ.`; return false; }
}
function card(author, created, body) {
  const result = node('article', '', 'thread-card');
  const metadata = node('div', '', 'thread-meta');
  metadata.append(avatar(author), node('strong', author), node('span', `commented ${date(created)}`));
  result.append(metadata, node('p', body || 'Chưa có mô tả.', 'thread-body'));
  return result;
}
function renderDetail(data) {
  current = data.issue;
  $('detail-title').replaceChildren(document.createTextNode(`${current.title} `), node('span', `#${current.number}`, 'issue-number'));
  $('detail-state').textContent = current.status === 'open' ? 'Open' : 'Closed';
  $('detail-state').className = `badge ${current.status}`;
  $('detail-meta').textContent = `${current.author} đã mở · ${date(current.created_at)} · v${current.version}`;
  $('sidebar-author').replaceChildren(avatar(current.author), node('span', current.author));
  $('sidebar-status').textContent = current.status === 'open' ? 'Open' : 'Closed';
  $('sidebar-comments').textContent = `${data.comments.length} bình luận`;
  $('sidebar-created').textContent = date(current.created_at);
  $('sidebar-updated').textContent = date(current.updated_at);
  $('participants').replaceChildren(...[...new Set([current.author,...data.comments.map(comment => comment.author)])].map(avatar));
  $('toggle').textContent = current.status === 'open' ? 'Đóng issue' : 'Mở lại issue';
  $('thread').replaceChildren(card(current.author, current.created_at, current.description));
  data.comments.forEach(comment => $('thread').append(card(comment.author, comment.created_at, comment.body)));
  $('history').replaceChildren();
  const actions = {created: 'Tạo issue', edited: 'Chỉnh sửa issue', closed: 'Đóng issue', reopened: 'Mở lại issue', commented: 'Thêm bình luận'};
  for (const entry of data.history) {
    const line = node('div', '', 'history-item');
    line.append(node('span', `${actions[entry.action]} · ${entry.after_data.author}`), node('small', date(entry.created_at)));
    $('history').append(line);
  }
}
async function openDetail(id) {
  try {
    const data = await api(`/api/issues/${id}`);
    renderDetail(data);
    $('detail-message').textContent = '';
    $('comment-message').textContent = '';
    const draft = read(commentKey(id));
    commentPending = draft?.pending || null;
    $('comment-author').value = draft?.author || '';
    $('comment-body').value = draft?.body || '';
    $('comment-draft-info').textContent = draft ? 'Đã khôi phục bản nháp bình luận. Kiểm tra trước khi gửi.' : '';
    history.replaceState(null, '', `#issue=${id}`);
    document.body.classList.add('detail-mode');
    if (!$('detail').open) $('detail').show();
    window.scrollTo(0,0);
    $('detail-title').focus({preventScroll:true});
  } catch (error) { $('notice').textContent = error.message; }
}
function openEditor(issue = null, draft = null) {
  if (!canWrite && !draft) return;
  const previous = read(issueKey);
  if (!draft && previous && !confirm('Có bản nháp issue chưa lưu. Thay bằng nội dung này?')) return;
  if ($('detail').open) { saveCommentDraft(); $('detail').close(); }
  editing = issue ? {...issue} : null;
  issuePending = draft?.pending || null;
  const values = draft || issue || {author: '', title: '', description: '', status: 'open'};
  if (draft?.id) editing = {id: draft.id, version: draft.version};
  $('issue-author').value = values.author;
  $('title').value = values.title;
  $('description').value = values.description;
  $('status').value = values.status;
  $('status').disabled = !editing;
  $('editor-heading').textContent = editing ? 'Chỉnh sửa issue' : 'Tạo issue';
  $('save').textContent = editing ? 'Lưu thay đổi' : 'Tạo issue';
  $('reload').hidden = !editing;
  $('form-message').textContent = '';
  $('draft-info').textContent = draft ? 'Đã khôi phục bản nháp. Kiểm tra nội dung trước khi lưu.' : '';
  history.replaceState(null, '', location.pathname);
  $('editor').showModal();
  applyAccess();
}
function busyForm(form, busy) { for (const control of form.elements) control.disabled = busy; }
$('issue-form').oninput = saveIssueDraft;
$('issue-form').onsubmit = async event => {
  event.preventDefault();
  if (issueBusy) return;
  if (!canWrite) { $('form-message').textContent = 'Database chưa cho phép ghi. Bản nháp vẫn được giữ trên trình duyệt.'; return; }
  const snapshot = issueSnapshot();
  if (!issuePending) issuePending = {snapshot, request_id: crypto.randomUUID()};
  saveIssueDraft();
  const sent = issuePending;
  issueBusy = true; busyForm($('issue-form'), true); $('save').textContent = 'Đang lưu…';
  try {
    const {id, ...fields} = sent.snapshot;
    const result = await api(id ? `/api/issues/${id}` : '/api/issues', {
      method: id ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...fields, request_id: sent.request_id})});
    issuePending = null;
    if (JSON.stringify(snapshot) !== JSON.stringify(sent.snapshot)) {
      editing = result; saveIssueDraft();
      $('form-message').textContent = 'Đã xác nhận lần lưu trước. Nội dung mới vẫn chưa lưu; bấm Lưu lần nữa.';
    } else {
      store(issueKey, null); $('editor').close(); await load(); await openDetail(result.id);
    }
  } catch (error) {
    if (error.definitive) issuePending = null;
    saveIssueDraft(); $('form-message').textContent = error.message;
  } finally {
    issueBusy = false; busyForm($('issue-form'), false); $('status').disabled = !editing;
    $('save').textContent = editing ? 'Lưu thay đổi' : 'Tạo issue';
    applyAccess();
  }
};
$('comment-form').oninput = saveCommentDraft;
$('comment-form').onsubmit = async event => {
  event.preventDefault();
  if (commentBusy || !current) return;
  if (!canWrite) { $('comment-message').textContent = 'Database đang chỉ đọc hoặc chưa kết nối. Bản nháp vẫn được giữ.'; return; }
  const id = current.id, snapshot = commentSnapshot();
  if (!commentPending) commentPending = {snapshot, request_id: crypto.randomUUID()};
  saveCommentDraft(); const sent = commentPending;
  commentBusy = true; busyForm($('comment-form'), true); $('comment-save').textContent = 'Đang gửi…';
  $('detail-close').disabled = true; $('edit').disabled = true; $('toggle').disabled = true;
  try {
    await api(`/api/issues/${id}/comments`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...sent.snapshot, request_id: sent.request_id})});
    commentPending = null;
    if (JSON.stringify(snapshot) === JSON.stringify(sent.snapshot)) {
      $('comment-body').value = ''; saveCommentDraft(); $('comment-message').textContent = 'Đã gửi bình luận.';
    } else {
      saveCommentDraft(); $('comment-message').textContent = 'Đã xác nhận bình luận trước. Nội dung mới chưa gửi; bấm Gửi lần nữa.';
    }
    try { renderDetail(await api(`/api/issues/${id}`)); } catch { $('detail-message').textContent = 'Đã gửi; chưa tải được nội dung mới. Hãy mở lại issue.'; }
    await load();
  } catch (error) {
    if (error.definitive) commentPending = null;
    saveCommentDraft(); $('comment-message').textContent = error.message;
  } finally {
    commentBusy = false; busyForm($('comment-form'), false); $('comment-save').textContent = 'Gửi bình luận';
    $('detail-close').disabled = false; $('edit').disabled = false; $('toggle').disabled = false;
    applyAccess();
  }
};
$('new').onclick = () => openEditor();
$('edit').onclick = () => openEditor(current);
$('toggle').onclick = () => {
  if (!canWrite) return;
  openEditor(current);
  if ($('editor').open) { $('status').value = current.status === 'open' ? 'closed' : 'open'; saveIssueDraft(); $('issue-form').requestSubmit(); }
};
$('editor-close').onclick = () => { saveIssueDraft(); $('editor').close(); };
$('editor').oncancel = event => { if (issueBusy) event.preventDefault(); else saveIssueDraft(); };
$('detail-close').onclick = () => { saveCommentDraft(); $('detail').close(); history.replaceState(null, '', location.pathname); };
$('detail').oncancel = event => { if (commentBusy) event.preventDefault(); else { saveCommentDraft(); history.replaceState(null, '', location.pathname); } };
$('discard').onclick = () => { if (confirm('Bỏ bản nháp issue?')) { store(issueKey, null); issuePending = null; $('editor').close(); } };
$('comment-discard').onclick = () => {
  if (confirm('Bỏ bản nháp bình luận?')) { commentPending = null; $('comment-body').value = ''; saveCommentDraft(); }
};
$('reload').onclick = async () => {
  if (!confirm('Thay nội dung form bằng bản mới? Bản nháp hiện tại sẽ được thay thế.')) return;
  try {
    const data = await api(`/api/issues/${editing.id}`); editing = data.issue; issuePending = null;
    $('issue-author').value = editing.author; $('title').value = editing.title;
    $('description').value = editing.description; $('status').value = editing.status;
    saveIssueDraft(); $('form-message').textContent = 'Đã tải bản mới. Kiểm tra trước khi lưu.';
  } catch (error) { $('form-message').textContent = error.message; }
};
$('refresh').onclick = load; $('search').oninput = render;
$('author-filter').onchange = render; $('sort').onchange = render;
$('detail').addEventListener('close', () => document.body.classList.remove('detail-mode'));
document.querySelectorAll('.filter').forEach(button => button.onclick = () => {
  filter = button.dataset.filter;
  document.querySelectorAll('.filter').forEach(item => item.classList.toggle('selected', item === button)); render();
});
let lastOrigin = null;
async function watchOrigin() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store', signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw Error();
    const status = await response.json();
    if (typeof status.environment !== 'string' || typeof status.region !== 'string') throw Error();
    const name = ({aws: 'AWS', azure: 'Azure', local: 'Local'})[status.environment.toLowerCase()] || status.environment;
    lastOrigin = name; $('origin').dataset.state = 'connected';
    $('origin-label').textContent = `Môi trường phục vụ: ${name}`; $('origin-region').textContent = status.region;
    $('origin').title = 'Backend trả lời lần kiểm tra gần nhất; không phải trạng thái database hoặc toàn cluster.';
  } catch {
    $('origin').dataset.state = 'disconnected'; $('origin-label').textContent = 'Chưa kết nối được API';
    $('origin-region').textContent = lastOrigin ? `Lần cuối: ${lastOrigin}` : '';
    $('origin').title = 'Không xác định được máy chủ hiện tại. Tên lần cuối là dữ liệu cũ.';
  }
  setTimeout(watchOrigin, 5000);
}
async function start() {
  applyAccess(); watchOrigin(); watchDatabase(); await load();
  const draft = read(issueKey);
  if (draft) openEditor(null, draft);
  else {
    const match = location.hash.match(/^#issue=([0-9a-f-]{36})$/i);
    if (match) await openDetail(match[1]);
  }
}
function applyAccess() {
  $('new').disabled = !canWrite;
  $('edit').disabled = !canWrite || commentBusy;
  $('toggle').disabled = !canWrite || commentBusy;
  $('save').disabled = !canWrite || issueBusy;
  $('comment-save').disabled = !canWrite || commentBusy;
}
async function watchDatabase() {
  const previous = canWrite;
  try {
    const state = await api('/api/database/status');
    canWrite = state.connected === true && state.writable === true;
    $('database-mode').dataset.mode = state.role;
    $('database-mode').textContent = state.role === 'standby'
      ? `Standby · Chỉ đọc · ${state.replication_connected ? 'Đang nhận dữ liệu đồng bộ' : 'Mất kết nối đồng bộ; dữ liệu có thể chưa cập nhật'}`
      : canWrite ? 'Primary · Cho phép đọc và ghi' : 'Database chỉ đọc';
    if (state.role === 'standby' || previous !== canWrite) {
      await load();
      if ($('detail').open && current && !commentBusy) {
        try { renderDetail(await api(`/api/issues/${current.id}`)); }
        catch { $('detail-message').textContent = 'Chưa tải được dữ liệu mới.'; }
      }
    }
  } catch {
    canWrite = false;
    $('database-mode').dataset.mode = 'unavailable';
    $('database-mode').textContent = 'Chưa kết nối được database · Tạm khóa thao tác ghi';
  }
  applyAccess();
  setTimeout(watchDatabase, 5000);
}
start();
