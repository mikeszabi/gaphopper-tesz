'use strict';
const $=id=>document.getElementById(id);
let currentUser, credentialAvailability={}, modelRequest=0;
function showCredentials(){
  const available=credentialAvailability[$('provider').value];
  $('credential-status').textContent=available?'API-kulcs betöltve a szerveren.':'Ehhez a szolgáltatóhoz nincs API-kulcs betöltve. Ellenőrizd a gyökér .env fájlt, majd indítsd újra a szervert.';
}
async function loadModels(preferred=''){
  const request=++modelRequest,provider=$('provider').value;
  $('model').replaceChildren(new Option('Modelllista betöltése…',''));
  $('model').disabled=true;$('save-model').disabled=true;$('refresh-models').disabled=true;
  showCredentials();$('models-status').textContent='Modellek lekérése a szolgáltató API-jából…';
  try{
    const data=await api('models?provider='+encodeURIComponent(provider));
    if(request!==modelRequest)return;
    $('model').replaceChildren(new Option('Válassz API-ból lekért modellt…',''));
    for(const model of data.models)$('model').add(new Option(model,model));
    $('model').disabled=data.models.length===0;
    $('save-model').disabled=data.models.length===0;
    if(data.models.includes(preferred))$('model').value=preferred;
    $('models-status').textContent=data.models.length?data.models.length+' modell az aktuális kulccsal lekért API-listában.':'A szolgáltató nem adott vissza használható szöveges modellt.';
    if(preferred&&!data.models.includes(preferred))$('models-status').textContent+=' A mentett modell ('+preferred+') nem szerepel benne; válassz újat.';
  }catch(error){if(request!==modelRequest)return;$('model').replaceChildren(new Option('A modelllista nem érhető el',''));$('models-status').textContent=error.message;}
  finally{if(request===modelRequest)$('refresh-models').disabled=false;}
}
$('provider').onchange=()=>loadModels();
$('refresh-models').onclick=()=>loadModels($('model').value);
function item(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
async function loadUsers(){
  const data=await api('admin/users');$('users').replaceChildren();
  for(const user of data.items){
    const form=item('form');form.className='user-row';
    const name=item('strong',user.username+(user.username===currentUser.username?' · te':''));
    const role=item('select');role.setAttribute('aria-label',user.username+' szerepköre');role.add(new Option('Felhasználó','user'));role.add(new Option('Adminisztrátor','admin'));role.value=user.role;
    const active=item('select');active.setAttribute('aria-label',user.username+' állapota');active.add(new Option('Aktív','true'));active.add(new Option('Letiltott','false'));active.value=String(user.active);
    const password=item('input');password.type='password';password.placeholder='Új jelszó (opcionális)';password.autocomplete='new-password';password.minLength=8;password.maxLength=256;password.setAttribute('aria-label',user.username+' új jelszava');
    const save=item('button','Mentés');save.type='submit';save.className='secondary';
    form.append(name,role,active,password,save);
    form.onsubmit=async event=>{
      event.preventDefault();const body={};if(role.value!==user.role)body.role=role.value;if((active.value==='true')!==user.active)body.active=active.value==='true';if(password.value)body.password=password.value;
      if(!Object.keys(body).length){$('edit-status').textContent='Nincs módosítás.';return;}
      save.disabled=true;
      try{await api('admin/users/'+encodeURIComponent(user.username),body,'PATCH');if(user.username===currentUser.username){location.assign('/login.html');return;}$('edit-status').textContent='A felhasználó módosítva.';await loadUsers();}
      catch(error){$('edit-status').textContent=error.message;save.disabled=false;}
    };
    $('users').append(form);
  }
}
$('model-form').onsubmit=async event=>{event.preventDefault();$('save-model').disabled=true;$('model-status').textContent='Modell ellenőrzése rövid generálási próbával…';try{const saved=await api('config',{provider:$('provider').value,model:$('model').value},'PUT');credentialAvailability=saved.credentials_available||{};showCredentials();$('model-status').textContent=credentialAvailability[saved.default_provider]?'A modellbeállítás elmentve. Az elemzés indítható.':'A modellbeállítás elmentve, de az elemzéshez még API-kulcsot kell beállítani.';}catch(error){$('model-status').textContent=error.message;}finally{$('save-model').disabled=$('model').disabled;}};
$('user-form').onsubmit=async event=>{event.preventDefault();$('create-user').disabled=true;try{await api('admin/users',{username:$('new-username').value.trim(),password:$('new-password').value,role:$('new-role').value});$('user-form').reset();$('user-status').textContent='A felhasználó létrehozva.';await loadUsers();}catch(error){$('user-status').textContent=error.message;}finally{$('create-user').disabled=false;}};
requireUser().then(async user=>{currentUser=user;if(user.role!=='admin'){$('page-status').textContent='A konfigurációhoz adminisztrátori jogosultság szükséges.';return;}$('config-main').hidden=false;const config=await api('config');$('provider').value=config.default_provider;credentialAvailability=config.credentials_available||{};showCredentials();if(!config.default_model)$('model-status').textContent='Még nincs alapmodell mentve. Válassz az API-modelllistából, majd mentsd a beállítást.';await Promise.all([loadModels(config.default_model),loadUsers()]);}).catch(error=>{$('page-status').textContent=error.message;});
