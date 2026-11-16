'use strict';
async function api(path, body, method) {
  const headers = {'X-Gaphopper-Client':'web'};
  const options = {method:method || (body ? 'POST':'GET'),headers,credentials:'same-origin'};
  if (body instanceof FormData) options.body=body;
  else if (body !== undefined) {headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const response=await fetch('/api/v1/'+path,options);
  const data=await response.json();
  if (!response.ok) {
    if (response.status===401 && !path.startsWith('auth/login')) location.assign('/login.html');
    throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
  }
  return data;
}
async function requireUser() {
  const user=await api('auth/me');
  const label=document.getElementById('current-user');
  if (label) label.textContent=user.username;
  const config=document.getElementById('config-link');
  if (config) config.hidden=user.role!=='admin';
  const logout=document.getElementById('logout');
  if (logout) logout.onclick=async()=>{await api('auth/logout',{});location.assign('/login.html');};
  document.body.classList.add('authenticated');
  return user;
}
