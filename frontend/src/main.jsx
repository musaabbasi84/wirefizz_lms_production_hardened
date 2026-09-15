import React,{useEffect,useMemo,useState}from'react';import{createRoot}from'react-dom/client';import'./styles.css';
const API=import.meta.env.VITE_API_URL||'';
let refreshInFlight=null;
const readCookie=name=>{const row=document.cookie.split('; ').find(x=>x.startsWith(`${name}=`));return row?decodeURIComponent(row.slice(name.length+1)):null};
const performRefresh=async()=>{
  if(refreshInFlight)return refreshInFlight;
  const csrf=readCookie('wf_refresh_csrf');
  if(!csrf)throw new Error('No refresh session');
  refreshInFlight=(async()=>{
    const r=await fetch(`${API}/api/auth/refresh`,{method:'POST',credentials:'include',headers:{'X-CSRF-TOKEN':csrf}});
    let d=null;try{d=await r.json()}catch{}
    if(!r.ok)throw new Error(d?.error||`Refresh failed (${r.status})`);
    return d;
  })().finally(()=>{refreshInFlight=null});
  return refreshInFlight;
};
const request=async(path,opts={},retry=true)=>{
  const o={credentials:'include',...opts,headers:{...(opts.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(opts.headers||{})}};
  const m=(o.method||'GET').toUpperCase();
  if(m!=='GET'&&m!=='HEAD'&&m!=='OPTIONS'){
    const csrf=readCookie(path==='/api/auth/refresh'?'wf_refresh_csrf':'wf_access_csrf');
    if(csrf)o.headers['X-CSRF-TOKEN']=csrf;
  }
  const r=await fetch(`${API}${path}`,o);
  let d=null;try{d=await r.json()}catch{}
  if(r.status===401&&retry&&path!=='/api/auth/refresh'&&readCookie('wf_refresh_csrf')){
    try{await performRefresh();return request(path,opts,false)}catch{
      window.dispatchEvent(new CustomEvent('wf:auth-expired'));
    }
  }
  if(!r.ok)throw new Error(d?.error||`Request failed (${r.status})`);
  return d;
};
const get=path=>request(path);const post=(path,body)=>request(path,{method:'POST',body:JSON.stringify(body)});const patch=(path,body)=>request(path,{method:'PATCH',body:JSON.stringify(body)});const del=path=>request(path,{method:'DELETE'});
const money=x=>x??0;const fmtDate=x=>x?new Date(x).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}):'—';const pct=x=>`${Number(x||0).toFixed(1)}%`;
function Avatar({user,size=40}){return user?.profile_picture?<img className="avatar" style={{width:size,height:size}} src={`${API}${user.profile_picture}`} alt=""/>:<div className="avatar placeholder" style={{width:size,height:size}}>{(user?.full_name||'?').split(' ').map(x=>x[0]).slice(0,2).join('').toUpperCase()}</div>}
function Toast({msg,onClose}){if(!msg)return null;return <div className="toast"><span>{msg}</span><button onClick={onClose}>×</button></div>}
function Shell({user,onLogout,children,setPage}){const admin=user.role==='admin';const navigate=page=>{setPage(page);window.setTimeout(()=>window.scrollTo({top:0,behavior:'smooth'}),0)};const[notif,setNotif]=useState([]);const[open,setOpen]=useState(false);const refresh=async()=>{try{const d=await get('/api/notifications?per_page=12');setNotif(d.notifications)}catch{}};useEffect(()=>{refresh();const i=setInterval(refresh,45000);return()=>clearInterval(i)},[]);return <div className="app-shell"><aside className="sidebar"><div className="brand"><img src="https://wirefizz.github.io/logo.png" alt="WireFizz"/><span>LMS</span></div><div className="user-mini"><Avatar user={user} size={42}/><div><strong>{user.full_name}</strong><small>{admin?'Administrator':'Campus Ambassador'}</small></div></div><nav>{admin?<><Nav onClick={()=>navigate('dashboard')} icon="⌂">Dashboard</Nav><Nav onClick={()=>navigate('leads')} icon="▣">All Leads</Nav><Nav onClick={()=>navigate('analytics')} icon="◈">Analytics</Nav><Nav onClick={()=>navigate('employees')} icon="◎">Employees</Nav><Nav onClick={()=>navigate('tasks')} icon="✓">Tasks</Nav><Nav onClick={()=>navigate('audit')} icon="◌">Audit Log</Nav></>:<><Nav onClick={()=>navigate('dashboard')} icon="⌂">Dashboard</Nav><Nav onClick={()=>navigate('leads')} icon="▣">My Leads</Nav><Nav onClick={()=>navigate('add')} icon="＋">Add Lead</Nav><Nav onClick={()=>navigate('tasks')} icon="✓">Follow-ups</Nav><Nav onClick={()=>navigate('performance')} icon="◈">Performance</Nav></>}<Nav onClick={()=>navigate('profile')} icon="○">Profile</Nav></nav><div className="side-foot"><span>WireFizz · Precision over speed</span><button onClick={onLogout}>Sign out</button></div></aside><main className="main"><header className="topbar"><div><span className="eyebrow">WIRE FIZZ · LMS</span><h1>{admin?'Operations control center':'Campus ambassador workspace'}</h1></div><div className="top-actions"><button className="icon-btn" onClick={()=>setOpen(!open)}>◔{notif.some(x=>!x.is_read)&&<i/>}</button><Avatar user={user}/></div>{open&&<div className="notifications"><div className="notif-head"><strong>Notifications</strong><button onClick={async()=>{await post('/api/notifications/read-all',{});refresh()}}>Mark all read</button></div>{notif.length?notif.map(n=><div className={n.is_read?'notif':'notif unread'} key={n.id}><strong>{n.title}</strong><span>{n.message}</span><small>{fmtDate(n.created_at)}</small></div>):<div className="empty">No notifications.</div>}</div>}</header><section className="content">{children}</section></main></div>}
function Nav({children,onClick,icon}){return <button className="nav-btn" onClick={onClick}><span>{icon}</span>{children}</button>}
function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({
    full_name: "",
    email: "",
    phone: "",
    password: "",
  });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setLoading(true);

    try {
      if (mode === "register") {
        await post("/api/auth/register", {
          full_name: form.full_name,
          email: form.email,
          phone: form.phone,
          password: form.password,
        });

        setMode("login");
        setForm({
          full_name: "",
          email: form.email,
          phone: "",
          password: "",
        });
        setErr("Account created successfully. Please sign in.");
        return;
      }

      const data = await post("/api/auth/login", {
        email: form.email,
        password: form.password,
      });

      onLogin(data.user);
    } catch (e) {
      setErr(e?.message || "Authentication failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth-panel">
        <div className="auth-brand">
          <img
            src="https://wirefizz.github.io/logo.png"
            alt="WireFizz"
          />
          <span>LMS</span>
        </div>

        <div className="eyebrow">NEXT-GEN OPERATIONS</div>

        <h1>
          {mode === "login" ? "Welcome back." : "Join WireFizz."}
        </h1>

        <p className="muted">
          A practical admissions and lead-management workspace built for
          measurable growth.
        </p>

        {err && <div className="alert">{err}</div>}

        <form onSubmit={submit}>
          {mode === "register" && (
            <>
              <label>
                Full name
                <input
                  type="text"
                  autoComplete="name"
                  value={form.full_name}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      full_name: e.target.value,
                    })
                  }
                  required
                />
              </label>

              <label>
                Phone
                <input
                  type="tel"
                  autoComplete="tel"
                  value={form.phone}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      phone: e.target.value,
                    })
                  }
                />
              </label>
            </>
          )}

          <label>
            Work email
            <input
              type="email"
              autoComplete="email"
              value={form.email}
              onChange={(e) =>
                setForm({
                  ...form,
                  email: e.target.value,
                })
              }
              required
            />
          </label>

          <label>
            Password
            <input
              type="password"
              autoComplete={
                mode === "login"
                  ? "current-password"
                  : "new-password"
              }
              minLength={8}
              value={form.password}
              onChange={(e) =>
                setForm({
                  ...form,
                  password: e.target.value,
                })
              }
              required
            />
          </label>

          <button
            className="primary full"
            type="submit"
            disabled={loading}
          >
            {loading
              ? "Please wait..."
              : mode === "login"
              ? "Sign in"
              : "Create account"}
          </button>
        </form>

        <button
          className="link-btn"
          type="button"
          onClick={() => {
            setMode(
              mode === "login"
                ? "register"
                : "login"
            );
            setErr("");
          }}
        >
          {mode === "login"
            ? "Create an ambassador account"
            : "Back to sign in"}
        </button>
      </div>

      <div className="auth-art">
        <div className="grid-lines" />
        <div>
          <span className="eyebrow">WIRE FIZZ</span>
          <h2>Build your next intelligent system.</h2>
          <p>
            Centralize campus leads, approvals, follow-ups and performance in
            one disciplined workspace.
          </p>
        </div>
      </div>
    </div>
  );
}function Cards({stats,admin}){const cards=admin?[['Total leads',stats.total,''],['Pending review',stats.pending,'attention'],['Accepted',stats.accepted,'good'],['Converted',stats.converted,'good'],['Conversion',pct(stats.conversion_rate),''],['Active employees',stats.ambassadors,'']]:[['My leads',stats.total,''],['Pending',stats.pending,'attention'],['Accepted',stats.accepted,'good'],['Converted',stats.converted,'good'],['Conversion',pct(stats.accepted?stats.converted/stats.accepted*100:0),''],['Due follow-ups',stats.due_followups,'attention']];return <div className="cards">{cards.map(c=><div className={`card ${c[2]}`} key={c[0]}><span>{c[0]}</span><strong>{money(c[1])}</strong></div>)}</div>}
function Dashboard({user,setPage}){const[stats,setStats]=useState({});const[leads,setLeads]=useState([]);const[msg,setMsg]=useState('');useEffect(()=>{Promise.all([get('/api/dashboard/stats'),get('/api/leads?per_page=8')]).then(([a,b])=>{setStats(a);setLeads(b.leads)}).catch(e=>setMsg(e.message))},[]);return <><div className="hero"><div><span className="eyebrow">{user.role==='admin'?'SYSTEM OVERVIEW':'YOUR PERFORMANCE'}</span><h2>{user.role==='admin'?'Everything in one control room.':'Turn conversations into conversions.'}</h2><p>Track the work that matters, keep every lead accountable and never lose the next follow-up.</p></div>{user.role==='ambassador'&&<button className="primary" onClick={()=>setPage('add')}>＋ Add lead</button>}</div>{msg&&<div className="alert">{msg}</div>}<Cards stats={stats} admin={user.role==='admin'}/><div className="two-col"><div className="panel"><div className="panel-head"><div><span className="eyebrow">RECENT LEADS</span><h3>Latest records</h3></div><button className="ghost" onClick={()=>setPage('leads')}>View all</button></div><LeadTable leads={leads} admin={user.role==='admin'} compact/></div><div className="panel"><span className="eyebrow">QUICK ACTIONS</span><h3>Move work forward</h3><div className="quick-grid">{(user.role==='admin'?[['▣','Review leads','leads'],['◎','Manage employees','employees'],['◈','Open analytics','analytics'],['✓','Follow-up queue','tasks']]:[['＋','Add a lead','add'],['▣','My leads','leads'],['✓','Follow-ups','tasks'],['◈','Performance','performance']]).map(x=><button key={x[2]} onClick={()=>setPage(x[2])}><span>{x[0]}</span><b>{x[1]}</b><small>Open workspace →</small></button>)}</div></div></div></>}
function LeadTable({leads,onOpen,onDelete,admin,compact}){return <div className="table-wrap"><table><thead><tr><th>Lead</th>{!compact&&<th>Employee</th>}<th>Review</th><th>Pipeline</th><th>Priority</th><th>Created</th>{admin&&<th/>}</tr></thead><tbody>{leads.length?leads.map(l=><tr key={l.id} onClick={()=>onOpen&&onOpen(l.id)}><td><strong>{l.full_name}</strong><small>{l.phone} · {l.institution||'—'}</small></td>{!compact&&<td>{l.ambassador_name||'—'}</td>}<td><Status value={l.approval_status}/></td><td><Status value={l.status}/></td><td><Status value={l.priority}/></td><td>{fmtDate(l.created_at)}</td>{admin&&<td><button className="danger ghost" onClick={e=>{e.stopPropagation();onDelete?.(l.id)}}>Delete</button></td>}</tr>):<tr><td colSpan="7"><div className="empty">No leads found.</div></td></tr>}</tbody></table></div>}
class ErrorBoundary extends React.Component{constructor(p){super(p);this.state={error:null}}static getDerivedStateFromError(error){return {error}}componentDidCatch(error,info){console.error("WireFizz render error",error,info)}render(){if(this.state.error)return <div className="error-screen"><div className="panel"><span className="eyebrow">APPLICATION ERROR</span><h2>Something went wrong</h2><p className="muted">The page could not be rendered. Your data and session are still intact.</p><pre>{this.state.error?.message||String(this.state.error)}</pre><button className="primary" onClick={()=>this.setState({error:null})}>Try again</button></div></div>;return this.props.children}}
function Status({value}){return <span className={`chip ${value}`}>{String(value).replace('_',' ')}</span>}
function LeadImport({setPage}){const[file,setFile]=useState(null);const[msg,setMsg]=useState('');const submit=async e=>{e.preventDefault();if(!file)return;const fd=new FormData();fd.append('file',file);try{const d=await request('/api/admin/import/leads',{method:'POST',body:fd});setMsg(`Import complete: ${d.created} created, ${d.skipped} skipped.`)}catch(e){setMsg(e.message)}};return <><div className="page-head"><div><span className="eyebrow">DATA OPERATIONS</span><h2>Import leads</h2></div><button className="ghost-btn" onClick={()=>setPage('leads')}>← Back</button></div><div className="panel form-panel">{msg&&<div className="alert">{msg}</div>}<p className="muted">CSV columns: <b>full_name, phone</b> plus optional email, city, institution, program_interest, source, notes, status, priority, ambassador_email.</p><form onSubmit={submit}><label className="file-drop">Choose CSV<input type="file" accept=".csv,text/csv" onChange={e=>setFile(e.target.files[0])}/></label><button className="primary">Import leads</button></form></div></>};
function Leads({user,setPage}){const[items,setItems]=useState([]);const[meta,setMeta]=useState({});const[q,setQ]=useState('');const[filters,setFilters]=useState({approval_status:'',status:'',priority:''});const[toast,setToast]=useState('');const load=async(page=1,search=q)=>{const p=new URLSearchParams({page,per_page:15,search,...filters});const d=await get(`/api/leads?${p}`);setItems(d.leads);setMeta(d.pagination)};useEffect(()=>{const t=setTimeout(()=>load(1,q),350);return()=>clearTimeout(t)},[q,filters.approval_status,filters.status,filters.priority]);const open=id=>setPage(`lead:${id}`);const remove=async id=>{if(!confirm('Delete this lead permanently?'))return;try{await del(`/api/leads/${id}`);setToast('Lead deleted.');load(meta.page)}catch(e){setToast(e.message)}};return <><div className="page-head"><div><span className="eyebrow">{user.role==='admin'?'GLOBAL PIPELINE':'YOUR PIPELINE'}</span><h2>{user.role==='admin'?'All leads':'My leads'}</h2></div><div className="head-actions">{user.role==='ambassador'&&<button className="primary" onClick={()=>setPage('add')}>＋ Add lead</button>}{user.role==='admin'&&<><a className="ghost-btn" href={`${API}/api/admin/export/leads.csv`} target="_blank">Export CSV</a><button className="ghost-btn" onClick={()=>setPage('import')}>Import CSV</button></>}</div></div><div className="filters"><input placeholder="Search name, phone, institution…" value={q} onChange={e=>setQ(e.target.value)}/><select value={filters.approval_status} onChange={e=>setFilters({...filters,approval_status:e.target.value})}><option value="">All review</option><option value="pending">Pending</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option></select><select value={filters.status} onChange={e=>setFilters({...filters,status:e.target.value})}><option value="">All stages</option>{['new','contacted','qualified','follow_up','converted'].map(x=><option value={x} key={x}>{x.replace('_',' ')}</option>)}</select><select value={filters.priority} onChange={e=>setFilters({...filters,priority:e.target.value})}><option value="">All priority</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></div><div className="panel"><LeadTable leads={items} admin={user.role==='admin'} onOpen={open} onDelete={remove}/><Paginator meta={meta} load={load}/></div><Toast msg={toast} onClose={()=>setToast('')}/></>}
function Paginator({meta,load}){if(!meta.pages||meta.pages<=1)return null;return <div className="pager"><button disabled={meta.page<=1} onClick={()=>load(meta.page-1)}>← Prev</button><span>Page {meta.page} of {meta.pages}</span><button disabled={meta.page>=meta.pages} onClick={()=>load(meta.page+1)}>Next →</button></div>}
function AddLead({setPage}){const[f,setF]=useState({full_name:'',phone:'',email:'',city:'',institution:'',program_interest:'',source:'Campus',priority:'medium',next_follow_up_at:'',notes:''});const[msg,setMsg]=useState('');const submit=async e=>{e.preventDefault();setMsg('');try{const d=await post('/api/leads',f);setMsg('Lead submitted for admin review.');setTimeout(()=>setPage(`lead:${d.lead.id}`),500)}catch(e){setMsg(e.message)}};return <><div className="page-head"><div><span className="eyebrow">CAPTURE</span><h2>Add a lead</h2></div></div><div className="panel form-panel">{msg&&<div className="alert">{msg}</div>}<form onSubmit={submit} className="form-grid">{[['full_name','Full name'],['phone','Phone'],['email','Email'],['city','City'],['institution','Institution'],['program_interest','Program interest'],['source','Source']].map(([k,l])=><label key={k}>{l}<input value={f[k]} onChange={e=>setF({...f,[k]:e.target.value})} required={k==='full_name'||k==='phone'}/></label>)}<label>Priority<select value={f.priority} onChange={e=>setF({...f,priority:e.target.value})}><option>low</option><option>medium</option><option>high</option></select></label><label>Next follow-up<input type="datetime-local" value={f.next_follow_up_at} onChange={e=>setF({...f,next_follow_up_at:e.target.value})}/></label><label className="full-row">Notes<textarea rows="5" value={f.notes} onChange={e=>setF({...f,notes:e.target.value})}/></label><div className="full-row"><button className="primary">Submit lead for review</button></div></form></div></>}
function LeadDetail({id,user,setPage}){const[l,setL]=useState(null);const[a,setA]=useState('');const[tasks,setTasks]=useState([]);const[msg,setMsg]=useState('');const[act,setAct]=useState({activity_type:'note',note:''});const load=()=>get(`/api/leads/${id}`).then(d=>{setL(d.lead);setA(d.activities);setTasks(d.tasks)}).catch(e=>setMsg(e.message));useEffect(()=>{load()},[id]);if(!l)return <div className="panel">{msg||'Loading…'}</div>;const update=async patchData=>{try{const d=await patch(`/api/leads/${id}`,patchData);setL(d.lead);setMsg('Updated.')}catch(e){setMsg(e.message)}};const addAct=async e=>{e.preventDefault();try{await post(`/api/leads/${id}/activities`,act);setAct({activity_type:'note',note:''});load()}catch(e){setMsg(e.message)}};const addTask=async()=>{const title=prompt('Task title');if(!title)return;const due=prompt('Due date ISO, e.g. 2026-09-20T10:00:00+05:00');if(!due)return;try{await post(`/api/leads/${id}/tasks`,{title,due_at:due,priority:'medium'});load()}catch(e){setMsg(e.message)}};return <><div className="page-head"><div><button className="back" onClick={()=>setPage('leads')}>← Back to leads</button><span className="eyebrow">LEAD #{l.id}</span><h2>{l.full_name}</h2><p className="muted">{l.phone} · {l.email||'No email'} · {l.institution||'No institution'}</p></div><div className="head-actions">{user.role==='admin'&&<><select value={l.approval_status} onChange={e=>update({approval_status:e.target.value})}><option>pending</option><option>accepted</option><option>rejected</option></select><select value={l.status} onChange={e=>update({status:e.target.value})}>{['new','contacted','qualified','follow_up','converted'].map(x=><option value={x} key={x}>{x.replace('_',' ')}</option>)}</select><select value={l.priority} onChange={e=>update({priority:e.target.value})}><option>low</option><option>medium</option><option>high</option></select></>}</div></div>{msg&&<div className="alert">{msg}</div>}<div className="two-col detail-cols"><div className="panel"><div className="panel-head"><div><span className="eyebrow">DETAILS</span><h3>Lead information</h3></div><Status value={l.approval_status}/></div><div className="detail-grid">{[['Employee',l.ambassador_name],['City',l.city],['Institution',l.institution],['Program',l.program_interest],['Source',l.source],['Priority',l.priority],['Last contacted',fmtDate(l.last_contacted_at)],['Next follow-up',fmtDate(l.next_follow_up_at)]].map(x=><div key={x[0]}><span>{x[0]}</span><b>{x[1]||'—'}</b></div>)}</div><div className="notes"><span className="eyebrow">NOTES</span><p>{l.notes||'No notes.'}</p></div></div><div className="panel"><div className="panel-head"><div><span className="eyebrow">ACTIVITY</span><h3>Timeline</h3></div><button className="ghost" onClick={addTask}>＋ Task</button></div><form className="activity-form" onSubmit={addAct}><select value={act.activity_type} onChange={e=>setAct({...act,activity_type:e.target.value})}><option>note</option><option>call</option><option>meeting</option><option>email</option></select><textarea placeholder="Add a note…" value={act.note} onChange={e=>setAct({...act,note:e.target.value})} required/><button className="primary">Add activity</button></form><div className="timeline">{a.map(x=><div className="timeline-item" key={x.id}><span className="dot"/><div><strong>{x.activity_type}</strong><small>{x.user_name} · {fmtDate(x.created_at)}</small><p>{x.note}</p></div></div>)}</div></div></div><div className="panel"><div className="panel-head"><div><span className="eyebrow">FOLLOW-UP TASKS</span><h3>Open and completed</h3></div></div>{tasks.length?tasks.map(t=><div className="task-row" key={t.id}><button className={`check ${t.is_completed?'done':''}`} onClick={async()=>{await patch(`/api/tasks/${t.id}`,{completed:!t.is_completed});load()}}>{t.is_completed?'✓':''}</button><div><strong>{t.title}</strong><span>{fmtDate(t.due_at)} · {t.priority}</span></div></div>):<div className="empty">No tasks for this lead.</div>}</div></>}
function Employees({setPage}){const[users,setUsers]=useState([]);const[q,setQ]=useState('');const[msg,setMsg]=useState('');const load=()=>get(`/api/admin/users?per_page=100&search=${encodeURIComponent(q)}`).then(d=>setUsers(d.users)).catch(e=>setMsg(e.message));useEffect(()=>{load()},[q]);const change=async(u,status)=>{try{await patch(`/api/admin/users/${u.id}`,{account_status:status});load()}catch(e){setMsg(e.message)}};const remove=async u=>{if(!confirm(`Delete ${u.full_name} and all their leads?`))return;try{await del(`/api/admin/users/${u.id}`);load()}catch(e){setMsg(e.message)}};return <><div className="page-head"><div><span className="eyebrow">PEOPLE</span><h2>Employees</h2></div><button className="primary" onClick={async()=>{const name=prompt('Employee name');const email=prompt('Email');const password=prompt('Temporary password (8+ chars)');if(name&&email&&password){try{await post('/api/admin/users',{full_name:name,email,password});load()}catch(e){setMsg(e.message)}}}}>＋ Add employee</button></div>{msg&&<div className="alert">{msg}</div>}<div className="filters"><input placeholder="Search employees…" value={q} onChange={e=>setQ(e.target.value)}/></div><div className="panel"><div className="people-list">{users.map(u=><div className="person-row" key={u.id}><Avatar user={u} size={48}/><div className="person-main"><strong>{u.full_name}</strong><span>{u.email} · {u.lead_count} leads</span></div><Status value={u.account_status}/><div className="row-actions"><button className="ghost" onClick={()=>change(u,u.account_status==='blocked'?'active':'blocked')}>{u.account_status==='blocked'?'Unblock':'Block'}</button><button className="danger ghost" onClick={()=>remove(u)}>Delete</button></div></div>)}{!users.length&&<div className="empty">No employees.</div>}</div></div></>}
function Analytics(){const[d,setD]=useState(null);const[from,setFrom]=useState('');const[to,setTo]=useState('');const load=()=>{const p=new URLSearchParams();if(from)p.set('from',from);if(to)p.set('to',to);get(`/api/admin/analytics?${p}`).then(setD).catch(()=>{})};useEffect(()=>{load()},[]);return <><div className="page-head"><div><span className="eyebrow">BUSINESS INTELLIGENCE</span><h2>Analytics</h2></div><div className="filters compact"><input type="date" value={from} onChange={e=>setFrom(e.target.value)}/><input type="date" value={to} onChange={e=>setTo(e.target.value)}/><button className="primary" onClick={load}>Apply</button></div></div>{d&&<><Cards stats={{...d.summary,ambassadors:d.employees.length}} admin/><div className="three-col"><div className="panel"><span className="eyebrow">PIPELINE</span><h3>Funnel</h3>{d.pipeline.map(x=><Bar key={x.status} label={x.status.replace('_',' ')} value={x.count} total={d.summary.total}/>)}</div><div className="panel"><span className="eyebrow">SOURCES</span><h3>Lead sources</h3>{d.sources.slice(0,8).map(x=><Bar key={x.source} label={x.source} value={x.count} total={d.summary.total}/>)}</div><div className="panel"><span className="eyebrow">DAILY VOLUME</span><h3>Submission trend</h3><div className="spark">{d.daily.map(x=><div key={x.date} title={`${x.date}: ${x.count}`} style={{height:`${Math.max(8,x.count/(Math.max(...d.daily.map(y=>y.count),1))*100)}%`}}/>)}</div></div></div><div className="panel"><div className="panel-head"><div><span className="eyebrow">LEADERBOARD</span><h3>Employee performance</h3></div></div><div className="leaderboard">{d.employees.map((x,i)=><div className="leader" key={x.id}><span className="rank">#{i+1}</span><strong>{x.name}</strong><span>{x.leads} leads</span><span>{x.converted} converted</span><b>{pct(x.conversion_rate)}</b></div>)}</div></div></>}</>}
function Bar({label,value,total}){return <div className="bar-row"><div><span>{label}</span><b>{value}</b></div><div className="bar"><i style={{width:`${total?Math.min(100,value/total*100):0}%`}}/></div></div>}
function Tasks(){const[items,setItems]=useState([]);const[msg,setMsg]=useState('');const load=()=>get('/api/tasks?per_page=100&completed=false').then(d=>setItems(d.tasks)).catch(e=>setMsg(e.message));useEffect(()=>{load()},[]);return <><div className="page-head"><div><span className="eyebrow">FOLLOW-UP CONTROL</span><h2>Tasks</h2></div></div>{msg&&<div className="alert">{msg}</div>}<div className="panel">{items.length?items.map(t=><div className="task-row" key={t.id}><button className="check" onClick={async()=>{await patch(`/api/tasks/${t.id}`,{completed:true});load()}}>✓</button><div><strong>{t.title}</strong><span>Lead #{t.lead_id} · {fmtDate(t.due_at)} · {t.assignee_name||'Unassigned'}</span></div><Status value={t.priority}/></div>):<div className="empty">No open follow-ups.</div>}</div></>}
function Performance(){const[d,setD]=useState(null);useEffect(() => {
  get('/api/performance')
    .then(setD)
    .catch(() => {});
}, []);return <><div className="page-head"><div><span className="eyebrow">PERFORMANCE</span><h2>My performance</h2></div></div>{d?<><div className="cards"><div className="card"><span>Leads</span><strong>{d.leads||0}</strong></div><div className="card"><span>Accepted</span><strong>{d.accepted||0}</strong></div><div className="card good"><span>Converted</span><strong>{d.converted||0}</strong></div><div className="card"><span>Conversion</span><strong>{pct(d.conversion_rate)}</strong></div><div className="card attention"><span>Pending</span><strong>{d.pending||0}</strong></div><div className="card"><span>Follow-ups due</span><strong>{d.due_followups||0}</strong></div></div><div className="two-col"><div className="panel"><span className="eyebrow">PIPELINE</span><h3>My lead funnel</h3>{d.pipeline.map(x=><Bar key={x.status} label={x.status.replace('_',' ')} value={x.count} total={d.leads}/>)}</div><div className="panel"><span className="eyebrow">RECENT ACTIVITY</span><h3>Recent leads</h3>{d.recent.map(x=><div className="task-row" key={x.id}><div><strong>{x.full_name}</strong><span>{x.status.replace('_',' ')} · {fmtDate(x.created_at)}</span></div><Status value={x.approval_status}/></div>)}</div></div></>:<div className="panel">Loading performance…</div>}</>}
function Profile({user,setUser}){const[f,setF]=useState({full_name:user.full_name,phone:user.phone||''});const[msg,setMsg]=useState('');const[pw,setPw]=useState({current_password:'',new_password:''});const save=async()=>{try{const d=await patch('/api/profile',f);setUser(d.user);setMsg('Profile saved.')}catch(e){setMsg(e.message)}};const upload=async e=>{const file=e.target.files[0];if(!file)return;const fd=new FormData();fd.append('file',file);try{const d=await request('/api/profile/avatar',{method:'POST',body:fd});setUser(d.user);setMsg('Profile picture updated.')}catch(e){setMsg(e.message)}};const removePic=async()=>{const d=await del('/api/profile/avatar');setUser(d.user);setMsg('Profile picture removed.')};const change=async()=>{try{await post('/api/auth/change-password',pw);setPw({current_password:'',new_password:''});setMsg('Password changed. You may need to sign in again on this device.')}catch(e){setMsg(e.message)}};return <><div className="page-head"><div><span className="eyebrow">ACCOUNT</span><h2>Profile</h2></div></div>{msg&&<div className="alert">{msg}</div>}<div className="two-col"><div className="panel profile-panel"><div className="profile-hero"><Avatar user={user} size={96}/><div><h3>{user.full_name}</h3><span>{user.email}</span></div></div><div className="upload-row"><label className="primary">Change picture<input type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={upload}/></label>{user.profile_picture&&<button className="ghost" onClick={removePic}>Remove</button>}</div><label>Full name<input value={f.full_name} onChange={e=>setF({...f,full_name:e.target.value})}/></label><label>Phone<input value={f.phone} onChange={e=>setF({...f,phone:e.target.value})}/></label><label>Email<input value={user.email} disabled/></label><button className="primary" onClick={save}>Save profile</button></div><div className="panel"><span className="eyebrow">SECURITY</span><h3>Change password</h3><label>Current password<input type="password" value={pw.current_password} onChange={e=>setPw({...pw,current_password:e.target.value})}/></label><label>New password<input type="password" value={pw.new_password} minLength="8" onChange={e=>setPw({...pw,new_password:e.target.value})}/></label><button className="primary" onClick={change}>Change password</button><div className="security-note"><strong>Session security</strong><p>WireFizz uses short-lived JWT access sessions with a refresh cookie. New tabs can reuse your active browser session.</p></div></div></div></>}
function Audit(){const[d,setD]=useState({logs:[]});const[msg,setMsg]=useState('');useEffect(()=>{get('/api/admin/audit-logs?per_page=100').then(setD).catch(e=>setMsg(e.message))},[]);return <><div className="page-head"><div><span className="eyebrow">GOVERNANCE</span><h2>Audit log</h2></div></div>{msg&&<div className="alert">{msg}</div>}<div className="panel"><div className="audit-list">{d.logs.map(x=><div className="audit-row" key={x.id}><div className="audit-dot"/><div><strong>{x.action.replaceAll('_',' ')}</strong><span>{x.user_name} · {fmtDate(x.created_at)}</span><p>{x.details||'—'}</p></div></div>)}</div></div></>}
function PageView({user,page,setPage,setUser}){if(page==='dashboard')return <Dashboard user={user} setPage={setPage}/>;if(page==='leads')return <Leads user={user} setPage={setPage}/>;if(page==='add')return <AddLead setPage={setPage}/>;if(page==='import')return user.role==='admin'?<LeadImport setPage={setPage}/>:<Dashboard user={user} setPage={setPage}/>;if(page==='analytics')return user.role==='admin'?<Analytics/>:<Dashboard user={user} setPage={setPage}/>;if(page==='employees')return user.role==='admin'?<Employees setPage={setPage}/>:<Dashboard user={user} setPage={setPage}/>;if(page==='tasks')return <Tasks/>;if(page==='performance')return user.role==='ambassador'?<Performance/>:<Dashboard user={user} setPage={setPage}/>;if(page==='profile')return <Profile user={user} setUser={setUser}/>;if(page==='audit')return user.role==='admin'?<Audit/>:<Dashboard user={user} setPage={setPage}/>;if(page.startsWith('lead:'))return <LeadDetail id={page.split(':')[1]} user={user} setPage={setPage}/>;return <div className="panel"><span className="eyebrow">WORKSPACE</span><h2>Page not found</h2><button className="primary" onClick={()=>setPage('dashboard')}>Back to dashboard</button></div>}
function App(){const[user,setUser]=useState(null);const[page,setPage]=useState('dashboard');const[boot,setBoot]=useState(true);useEffect(()=>{let cancelled=false;(async()=>{try{let d;try{d=await get('/api/auth/me')}catch(err){if(readCookie('wf_refresh_csrf')){await performRefresh();d=await get('/api/auth/me')}else throw err}if(!cancelled)setUser(d.user)}catch(err){if(!cancelled)setUser(null)}finally{if(!cancelled)setBoot(false)}})();const expired=()=>{setUser(null);setPage('dashboard')};window.addEventListener('wf:auth-expired',expired);return()=>{cancelled=true;window.removeEventListener('wf:auth-expired',expired)}},[]);if(boot)return <div className="loading">Loading WireFizz LMS…</div>;if(!user)return <Login onLogin={u=>{setUser(u);setPage('dashboard')}}/>;const logout=async()=>{try{await post('/api/auth/logout',{})}finally{setUser(null);setPage('dashboard')}};return <Shell user={user} onLogout={logout} setPage={setPage}><ErrorBoundary key={page}><PageView user={user} page={page} setPage={setPage} setUser={setUser}/></ErrorBoundary></Shell>}

createRoot(document.getElementById('root')).render(<App/>);
