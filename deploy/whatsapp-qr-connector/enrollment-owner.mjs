// Ownership of NEW browser enrollments; provider sessions remain with this worker.
import {existsSync,readFileSync,writeFileSync,renameSync,chmodSync} from 'node:fs';
export class EnrollmentOwners {
  constructor(path,legacyUser='user_owner') {
    this.path=path;this.legacyUser=legacyUser;this.auth=new Map();
    this.owners=existsSync(path)?JSON.parse(readFileSync(path,'utf8')):{};
    if(!this.owners||typeof this.owners!=='object'||Array.isArray(this.owners))throw Error('Invalid enrollment ownership');
  }
  owner(slot){return this.owners[slot]||this.legacyUser;}
  allowed(slot,user){return this.owner(slot)===user;}
  bind(slot,principal,headers){
    if(!principal||!principal.can_manage||typeof principal.user_id!=='string'||!principal.user_id)throw Error('Personal account login required');
    if(this.owners[slot]&&this.owners[slot]!==principal.user_id)throw Error('Enrollment belongs to another user');
    this.owners[slot]=principal.user_id;this.auth.set(slot,headers);
    const temporary=this.path+'.next';writeFileSync(temporary,JSON.stringify(this.owners),{mode:0o600});chmodSync(temporary,0o600);renameSync(temporary,this.path);
  }
  registrationHeaders(slot){
    const auth=this.auth.get(slot);
    if(auth)return auth;
    if(this.owner(slot)!==this.legacyUser)throw Error('User login expired before account registration');
    return null;
  }
  registered(slot){this.auth.delete(slot);}
  filter(rows,user){return rows.filter(row=>this.allowed(row.id,user));}
}
export async function enrollmentPrincipal(base,headers,fetcher){
  const cookie=String(headers.cookie||'');const authorization=String(headers.authorization||'');
  if(!cookie&&!authorization)throw Error('Войдите в свой UserIO');
  const auth=cookie?{Cookie:cookie}:{Authorization:authorization};
  const result=await fetcher(new URL('/v1/connection-principal',base),auth);
  if(!result||typeof result.user_id!=='string'||!result.user_id)throw Error('Войдите в свой UserIO');
  return {principal:result,headers:auth};
}
