'use strict';
document.getElementById('login-form').onsubmit=async event=>{
  event.preventDefault();const button=document.getElementById('login-submit');button.disabled=true;
  try {await api('auth/login',{username:document.getElementById('username').value.trim(),password:document.getElementById('password').value});location.assign('/');}
  catch(error){document.getElementById('login-status').textContent=error.message;}
  finally{button.disabled=false;}
};
