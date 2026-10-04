const $ = id => document.getElementById(id);
const names = {todo:'Chưa làm',doing:'Đang làm',done:'Hoàn thành'};
const key = 'taskflow:draft:v1';
let tasks=[], filter='all', editing=null, pending=null, busy=false;
const date = value => new Date(value).toLocaleString('vi-VN');
function element(tag, text, className=''){const node=document.createElement(tag);node.textContent=text;node.className=className;return node;}
async function api(path, options={}){const response=await fetch(path,options);let data;try{data=await response.json();}catch{throw Error('Không đọc được phản hồi. Hãy thử lại.');}if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Vui lòng kiểm tra nội dung nhập.');return data;}
function readDraft(){try{return JSON.parse(localStorage.getItem(key));}catch{return null;}}
function snapshot(){return {id:editing?.id||null,version:editing?.version||0,title:$('title').value,description:$('description').value,status:$('status').value};}
function storeDraft(){try{const value=snapshot();const unchanged=editing&&value.title===editing.title&&value.description===editing.description&&value.status===editing.status;const empty=!editing&&!value.title&&!value.description&&value.status==='todo';if(!pending&&(unchanged||empty)){localStorage.removeItem(key);$('draft-info').textContent='';return;}localStorage.setItem(key,JSON.stringify({...value,pending}));$('draft-info').textContent='Bản nháp được giữ trên trình duyệt này.';}catch{$('draft-info').textContent='Không lưu được bản nháp trên trình duyệt. Giữ trang mở để tránh mất nội dung.';}}
function render(){const query=$('search').value.toLocaleLowerCase('vi');const visible=tasks.filter(t=>(filter==='all'||t.status===filter)&&`${t.title} ${t.description}`.toLocaleLowerCase('vi').includes(query));$('total').textContent=tasks.length;$('doing').textContent=tasks.filter(t=>t.status==='doing').length;$('done').textContent=tasks.filter(t=>t.status==='done').length;$('list').replaceChildren();if(!visible.length){const box=element('div','','empty');box.append(element('h2',tasks.length?'Không tìm thấy công việc':'Bắt đầu với một công việc.'),element('p',tasks.length?'Thử từ khóa hoặc trạng thái khác.':'Tạo công việc đầu tiên và theo dõi từng bước tiến.'));$('list').append(box);return;}for(const task of visible){const row=element('button','','task');row.type='button';const content=element('span','','task-content');content.append(element('span',task.title,'task-title'),element('span',task.description||'Chưa có mô tả','task-description'));row.append(content,element('span',names[task.status],`badge ${task.status}`),element('span',date(task.updated_at),'date'));row.addEventListener('click',()=>open(task));$('list').append(row);}}
async function load(){try{tasks=await api('/api/tasks');render();$('notice').textContent='';}catch(error){$('notice').textContent=`${error.message} Danh sách hiện tại có thể chưa cập nhật.`;}}
async function history(id){$('history').textContent=id?'Đang tải…':'Chưa có thay đổi.';if(!id)return;try{const entries=await api(`/api/tasks/${id}/history`);if(editing?.id!==id)return;$('history').replaceChildren();for(const entry of entries){const line=element('div','','history-item');line.append(element('span',`${entry.before_data?'Cập nhật':'Tạo mới'} · ${entry.after_data.title} · ${names[entry.after_data.status]} · v${entry.after_data.version}`),element('small',date(entry.created_at)));$('history').append(line);}}catch(error){$('history').textContent=error.message;}}
function open(task=null, draft=null){const previous=readDraft();if(!draft&&previous&&(previous.title||previous.description||previous.pending)){if(!confirm('Có bản nháp chưa lưu. Thay bằng công việc này?'))return;}editing=task?{...task}:null;pending=draft?.pending||null;const values=draft||task||{title:'',description:'',status:'todo'};if(draft?.id)editing={id:draft.id,version:draft.version};$('title').value=values.title;$('description').value=values.description;$('status').value=values.status;$('heading').textContent=editing?'Chỉnh sửa công việc':'Tạo công việc';$('form-message').textContent='';$('draft-info').textContent=draft?'Đã khôi phục bản nháp. Kiểm tra nội dung trước khi lưu.':'';$('reload').hidden=!editing;$('editor').showModal();history(editing?.id);}
function setBusy(value){busy=value;for(const control of $('form').elements)control.disabled=value;$('save').textContent=value?'Đang lưu…':'Lưu công việc';}
$('form').addEventListener('input',()=>{storeDraft();});
$('form').addEventListener('submit',async event=>{event.preventDefault();if(busy)return;if(pending&&JSON.stringify(pending.snapshot)!==JSON.stringify(snapshot())){$('form-message').textContent='Yêu cầu trước chưa được xác nhận. Hệ thống sẽ kiểm tra lại trước; nội dung mới vẫn được giữ.';}const current=snapshot();if(!pending)pending={snapshot:current,request_id:crypto.randomUUID()};storeDraft();const sent=pending;setBusy(true);try{const {id,...fields}=sent.snapshot;const result=await api(id?`/api/tasks/${id}`:'/api/tasks',{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...fields,request_id:sent.request_id})});pending=null;if(JSON.stringify(current)!==JSON.stringify(sent.snapshot)){editing=result;storeDraft();$('form-message').textContent='Đã xác nhận lần lưu trước. Nội dung mới chưa lưu; bấm Lưu lần nữa.';}else{try{localStorage.removeItem(key);}catch{}$('editor').close();}await load();}catch(error){$('form-message').textContent=error.message;storeDraft();}finally{setBusy(false);}});
$('new').onclick=()=>open();$('close').onclick=()=>{storeDraft();$('editor').close();};$('editor').addEventListener('cancel',event=>{if(busy)event.preventDefault();else storeDraft();});$('discard').onclick=()=>{if(confirm('Bỏ bản nháp chưa lưu?')){try{localStorage.removeItem(key);}catch{}pending=null;$('editor').close();}};
$('reload').onclick=async()=>{if(!confirm('Tải dữ liệu mới vào form? Nội dung đang nhập sẽ được thay thế.'))return;try{await load();const latest=tasks.find(t=>t.id===editing?.id);if(!latest)throw Error('Không tìm thấy công việc.');pending=null;editing={...latest};$('title').value=latest.title;$('description').value=latest.description;$('status').value=latest.status;storeDraft();$('form-message').textContent='Đã tải bản mới. Kiểm tra rồi lưu.';history(latest.id);}catch(error){$('form-message').textContent=error.message;}};
$('refresh').onclick=load;$('search').oninput=render;document.querySelectorAll('.filter').forEach(button=>button.onclick=()=>{filter=button.dataset.filter;document.querySelectorAll('.filter').forEach(b=>b.classList.toggle('selected',b===button));render();});
let lastOrigin=null;
async function refreshOrigin(){
  try{
    const response=await fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(4000)});
    if(!response.ok)throw Error('status unavailable');
    const status=await response.json();
    if(typeof status.environment!=='string'||typeof status.region!=='string')throw Error('invalid status');
    const environment=status.environment.toLowerCase();
    const name=({aws:'AWS',azure:'Azure',local:'Local'})[environment]||status.environment;
    lastOrigin={name,region:status.region};
    $('origin').dataset.state='connected';
    $('origin-label').textContent=`Môi trường phục vụ: ${name}`;
    $('origin-region').textContent=status.region;
    $('origin').title=`Máy chủ trả lời lần kiểm tra gần nhất: ${name} · ${status.region}. Đây không phải trạng thái database hoặc toàn bộ cluster.`;
  }catch{
    $('origin').dataset.state='disconnected';
    $('origin-label').textContent='Chưa kết nối được API';
    $('origin-region').textContent=lastOrigin?`Lần cuối: ${lastOrigin.name}`:'';
    $('origin').title='Không xác định được máy chủ đang phục vụ. Dữ liệu lần cuối không phải trạng thái hiện tại.';
  }
}
async function watchOrigin(){await refreshOrigin();setTimeout(watchOrigin,5000);}
watchOrigin();
load();const draft=readDraft();if(draft&&(draft.title||draft.description||draft.pending))open(null,draft);
